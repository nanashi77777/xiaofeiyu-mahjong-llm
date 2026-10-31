"""Screenshot targets for canvas/WebGL clients, independent of the state input."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .domain import tile_index
from .native import InputDeferred


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    width: int
    height: int
    confidence: float
    label: str

    @property
    def center(self):
        return self.x+self.width/2, self.y+self.height/2


def decode_image(raw):
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("invalid local screenshot")
    return image


def glyph_similarity(region, patch):
    direct = float(cv2.minMaxLoc(cv2.matchTemplate(region, patch, cv2.TM_CCOEFF_NORMED))[1])
    # Canvas resizing resamples fine dot patterns. The same small blur on both
    # images removes that pixel-grid artifact while retaining glyph/color detail.
    softened = float(cv2.minMaxLoc(cv2.matchTemplate(
        cv2.GaussianBlur(region, (3, 3), .6), cv2.GaussianBlur(patch, (3, 3), .6),
        cv2.TM_CCOEFF_NORMED))[1])
    return max(direct, softened)


class ScreenLocator:
    def __init__(self, runtime:Path):
        self.folder = runtime / "calibration"
        self.path = self.folder / "profile.json"
        self.classifier = None
        self.hand_cache = None
        self.hand_cache_hits = 0
        self.hand_cache_misses = 0
        self.normalized_frame = None

    @staticmethod
    def hand_signature(state):
        return (state.match_id,state.round_uid,tuple(state.hand),state.drawn_index,
                tuple((m.id,m.kind,tuple(m.tiles)) for m in state.players[state.self_seat].melds),
                bool(state.public_events and state.public_events[-1]['kind']=='ActionNewRound'))

    def profile(self):
        if not self.path.exists():
            raise InputDeferred("WebGL screenshot calibration is missing")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def normalized(self,raw,profile):
        cached = self.normalized_frame
        size = (profile['width'],profile['height'])
        if cached and cached[0] is raw and cached[1] == size:
            return cached[2]
        image=decode_image(raw)
        height,width=image.shape[:2]
        expected=profile["width"]/profile["height"]
        if abs(width/height/expected-1)>.01:
            raise InputDeferred("viewport aspect ratio changed; recalibration required")
        normalized = cv2.resize(image,size)
        normalized.setflags(write=False)
        result = normalized,width/profile['width'],height/profile['height']
        self.normalized_frame = (raw,size,result)
        return result

    def save_crop(self, raw, label, box, *, hand_region=None):
        if not re.fullmatch(r"(?:tile_[0-9][mpsz]|button_[a-z0-9_]+|combo_[a-z0-9_]+)", label):
            raise ValueError("invalid calibration label")
        if label.startswith("tile_"):
            tile_index(label[5:])
        image = decode_image(raw)
        height,width = image.shape[:2]
        x,y,w,h = map(int,box)
        if not (0<=x<x+w<=width and 0<=y<y+h<=height and w>=8 and h>=8):
            raise ValueError("crop outside screenshot")
        self.folder.mkdir(parents=True,exist_ok=True)
        profile = self.profile() if self.path.exists() else {"version":"screen.v1","width":width,"height":height,"threshold":.94,"templates":{}}
        if (profile["width"],profile["height"]) != (width,height):
            raise ValueError("viewport differs from calibration")
        filename = label+".png"
        cv2.imencode(".png",image[y:y+h,x:x+w])[1].tofile(str(self.folder/filename))
        profile["templates"][label] = {"file":filename,"size":[w,h]}
        if hand_region:
            hx,hy,hw,hh = map(int,hand_region)
            if not (0<=hx<hx+hw<=width and 0<=hy<hy+hh<=height):
                raise ValueError("hand region outside screenshot")
            profile["hand_region"] = [hx,hy,hw,hh]
        self.path.write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding="utf-8",newline="\n")
        return {"label":label,"viewport":[width,height],"calibrated_labels":list(profile["templates"])}

    def matches(self, raw, labels, *, region=None, resampled=False):
        profile = self.profile()
        image,sx,sy = self.normalized(raw,profile)
        height,width = image.shape[:2]
        ox,oy,w,h = region or (0,0,width,height)
        search = image[oy:oy+h,ox:ox+w]
        boxes = []
        for label in labels:
            template = profile["templates"].get(label)
            if not template:
                raise InputDeferred("required visual label is not calibrated")
            variants = ([template] if 'file' in template else []) + template.get('variants', [])
            if not variants:
                raise InputDeferred('visual label has no calibrated glyph variants')
            for variant in variants:
                boxes.extend(self._patch_matches(search, variant, label, profile, ox, oy, sx, sy, resampled))
        # Two glyph variants may recognize the same physical button. Preserve
        # separate locations as ambiguity rather than choosing the best score.
        unique = []
        for box in sorted(boxes, key=lambda b:-b.confidence):
            if not any(box.label == other.label
                       and abs(box.center[0]-other.center[0]) < min(box.width,other.width)*.45
                       and abs(box.center[1]-other.center[1]) < min(box.height,other.height)*.45
                       for other in unique):
                unique.append(box)
        return sorted(unique,key=lambda b:(b.x,b.y,b.label))

    def _patch_matches(self, search, template, label, profile, ox, oy, sx, sy, resampled):
        patch = decode_image((self.folder/template["file"]).read_bytes())
        ph,pw = patch.shape[:2]
        if search.shape[0] < ph or search.shape[1] < pw:
            return []
        if float(np.std(patch)) < 3:
            raise InputDeferred("calibration patch has no identifying detail")
        result = cv2.matchTemplate(search,patch,cv2.TM_CCOEFF_NORMED)
        if resampled:
            result = np.maximum(result, cv2.matchTemplate(
                cv2.GaussianBlur(search,(3,3),.6),cv2.GaussianBlur(patch,(3,3),.6),
                cv2.TM_CCOEFF_NORMED))
        # Suppress adjacent pixel maxima for one physical tile/button.
        boxes = []
        for _ in range(20):
            _,value,_,point = cv2.minMaxLoc(result)
            if value < profile.get("threshold",.94):
                break
            x,y = point
            boxes.append(Box(round((x+ox)*sx),round((y+oy)*sy),round(pw*sx),round(ph*sy),value,label))
            result[max(0,y-ph//2):y+ph//2+1,max(0,x-pw//2):x+pw//2+1] = -1
        return boxes

    def hand(self, raw, state):
        signature = self.hand_signature(state)
        if state.public_events and state.public_events[-1]['kind'] == 'ActionNewRound':
            # Also accept pre-fix cached snapshots: initial dealing is a sorted
            # complete row, not an ActionDealTile draw instance.
            state = state.model_copy(update={'drawn_index':None})
        profile = self.profile()
        if "hand_region" not in profile:
            raise InputDeferred("hand region not calibrated")
        image,sx,sy = self.normalized(raw,profile)
        x,y,w,h = profile['hand_region']
        pixels = image[y:y+h,x:x+w]
        cache_key = (signature,sx,sy,self.path.stat().st_mtime_ns)
        cached = self.hand_cache
        if cached and cached['key'] == cache_key and np.array_equal(pixels,cached['pixels']):
            self.hand_cache_hits += 1
            return dict(cached['mapping'])
        self.hand_cache_misses += 1
        labels = ["tile_"+tile for tile in sorted(set(state.hand))]
        if profile.get("verified_sorted_row"):
            candidates=self.learn_sorted_row(raw,state,profile)
        else:
            candidates = self.matches(raw,labels,region=profile["hand_region"])
        boxes = []
        for box in sorted(candidates,key=lambda b:-b.confidence):
            if not any(abs(box.center[0]-b.center[0]) < min(box.width,b.width)*.6 and abs(box.center[1]-b.center[1]) < min(box.height,b.height)*.6 for b in boxes):
                boxes.append(box)
        boxes.sort(key=lambda b:b.x)
        if len(boxes) != len(state.hand):
            raise InputDeferred("recognized hand count differs from authoritative hand")
        actual = [b.label[5:] for b in boxes]
        if sorted(actual) != sorted(state.hand):
            raise InputDeferred("recognized hand tiles differ from authoritative hand")
        # Match physical instances, retaining the rightmost drawn slot separately.
        pool = list(enumerate(boxes))
        mapping = {}
        if state.drawn_index is not None:
            index,box = pool[-1]
            if box.label[5:] != state.hand[state.drawn_index]:
                raise InputDeferred("draw slot not verified")
            mapping[state.drawn_index] = box
            pool.pop()
        for index,tile in enumerate(state.hand):
            if index in mapping:
                continue
            selected = next(((i,b) for i,b in pool if b.label[5:] == tile),None)
            if not selected:
                raise InputDeferred("tile instance cannot be mapped")
            mapping[index] = selected[1]
            pool.remove(selected)
        self.hand_cache = {'key':(signature,sx,sy,self.path.stat().st_mtime_ns),
                           'pixels':pixels.copy(),'mapping':dict(mapping)}
        return mapping

    def learn_sorted_row(self,raw,state,profile):
        """Learn new glyphs only on a calibrated row with multiple visible anchors."""
        image,sx,sy=self.normalized(raw,profile)
        ox,oy,w,h=profile["hand_region"]
        hsv=cv2.cvtColor(image[oy:oy+h,ox:ox+w],cv2.COLOR_BGR2HSV)
        mask=cv2.inRange(hsv,(0,0,175),(180,55,255))
        contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        boxes=[]
        for contour in contours:
            x,y,bw,bh=cv2.boundingRect(contour)
            if 35<bw<85 and 65<bh<115:
                boxes.append((x+ox,y+oy,bw,bh))
        boxes.sort()
        if len(boxes)!=len(state.hand):
            raise InputDeferred("calibrated tile row segmentation differs from hand count")
        concealed=[t for i,t in enumerate(state.hand) if i!=state.drawn_index]
        ordered=sorted(concealed,key=lambda t:(tile_index(t),0 if t.startswith('0') else 1))
        if state.drawn_index is not None:
            ordered.append(state.hand[state.drawn_index])
        anchors=set()
        scores={}
        for tile,(x,y,bw,bh) in zip(ordered,boxes,strict=True):
            item=profile["templates"].get("tile_"+tile)
            if not item:
                continue
            patch=decode_image((self.folder/item["file"]).read_bytes())
            region=image[max(0,y+6):y+bh,x+2:x+bw-2]
            if region.shape[0]<patch.shape[0] or region.shape[1]<patch.shape[1]:
                raise InputDeferred("calibrated row glyph dimensions changed")
            similarity=glyph_similarity(region,patch)
            if similarity<profile.get("threshold",.94):
                raise InputDeferred("visible sorted row disagrees with authoritative ordering")
            anchors.add(tile)
            scores[(x,y)]=similarity
        if len(anchors)<3:
            raise InputDeferred("insufficient distinct known glyph anchors")
        changed=False
        for tile,(x,y,_bw,_bh) in zip(ordered,boxes,strict=True):
            label="tile_"+tile
            if label not in profile["templates"]:
                patch=image[y+12:y+90,x+8:x+56]
                cv2.imencode('.png',patch)[1].tofile(str(self.folder/(label+'.png')))
                profile["templates"][label]={"file":label+'.png',"size":[48,78],"source":"verified_sorted_row_anchors"}
                changed=True
        if changed:
            self.path.write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
        return [Box(round(x*sx),round(y*sy),round(bw*sx),round(bh*sy),scores.get((x,y),1.0),'tile_'+tile)
                for tile,(x,y,bw,bh) in zip(ordered,boxes,strict=True)]

    def button(self,raw,kind):
        found = self.matches(raw,["button_"+kind])
        if len(found) != 1:
            raise InputDeferred("button is absent or ambiguous")
        return found[0]

    def riichi_tile(self, raw, state, index):
        # The selection overlay dims every forbidden tile, so normal full-row
        # white-face segmentation is no longer valid. Only a server-offered
        # discard glyph may be bright; match the exact requested tile and keep
        # its occurrence/slot in the independently verified sorted row.
        options = [o for o in state.operation.raw['operation_list'] if o['type'] == 7]
        tile = state.hand[index]
        if not options or not any(tile in o.get('combination', []) for o in options):
            raise InputDeferred('riichi tile is not offered by the server')
        profile = self.profile()
        region = profile['hand_region']
        found = self.matches(raw, ['tile_'+tile], region=region, resampled=True)
        # Correlation alone is insensitive to brightness. A legal glyph must
        # also remain an enabled white face after the riichi overlay.
        image,sx,sy = self.normalized(raw,profile)
        ox,oy,w,h = region
        white = cv2.inRange(cv2.cvtColor(image[oy:oy+h,ox:ox+w],cv2.COLOR_BGR2HSV),
                            (0,0,175),(180,55,255))
        contours,_ = cv2.findContours(white,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        faces = [(ox+x,oy+y,bw,bh) for contour in contours for x,y,bw,bh in [cv2.boundingRect(contour)]
                 if 35<bw<85 and 65<bh<115 and cv2.contourArea(contour) > bw*bh*.7]
        found = [box for box in found if any(
            x-2 <= box.x/sx and y-2 <= box.y/sy and
            (box.x+box.width)/sx <= x+bw+2 and (box.y+box.height)/sy <= y+bh+2
            for x,y,bw,bh in faces)]
        if not found:
            raise InputDeferred('legal riichi discard glyph is not visible')
        found.sort(key=lambda b:b.x)
        if len(found) != state.hand.count(tile):
            raise InputDeferred('riichi tile occurrences are not completely visible')
        if state.drawn_index == index:
            return found[-1]
        same = [i for i,t in enumerate(state.hand) if t == tile and i != state.drawn_index]
        occurrence = same.index(index)
        if occurrence >= len(found):
            raise InputDeferred('riichi tile instance is not visible')
        return found[occurrence]

    def combo(self, raw, kind, tiles, offered=None):
        # An old matching crop cannot prove that all CURRENT choices are
        # visible. Always classify and reconcile the complete menu first.
        groups = self.menu_groups(raw)
        actual = [tuple(sorted(names)) for names,_ in groups]
        if offered is not None and (set(actual) != {tuple(sorted(t)) for t in offered}
                                    or len(actual) != len(set(actual))):
            raise InputDeferred('visible menu differs from complete server choices')
        found = [box for names,box in groups if sorted(names) == sorted(tiles)]
        if len(found) != 1:
            raise InputDeferred('selected meld combination is not uniquely visible')
        label = "combo_"+kind+"_"+"_".join(sorted(tiles))
        if label in self.profile()['templates']:
            crops = self.matches(raw,[label])
            if len(crops) > 1:
                raise InputDeferred('combination crop is ambiguous')
            if crops:
                x,y = crops[0].center
                group = found[0]
                if not (group.x <= x <= group.x+group.width and group.y <= y <= group.y+group.height):
                    raise InputDeferred('combination crop disagrees with classified menu')
                return crops[0]
        return found[0]

    def menu_groups(self, raw):
        profile = self.profile()
        if 'combo_region' not in profile:
            raise InputDeferred('combination menu region not calibrated')
        image,sx,sy = self.normalized(raw,profile)
        ox,oy,w,h = profile['combo_region']
        if not (0 <= ox < ox+w <= image.shape[1] and 0 <= oy < oy+h <= image.shape[0]):
            raise InputDeferred('combination menu region outside calibrated canvas')
        # A two-choice calibration is an anchor for the menu band, not its
        # maximum width. Longer menus extend sideways and may wrap upward.
        # Never include the concealed hand in this search band.
        hand = profile.get('hand_region')
        if hand:
            hand_top = hand[1]
            bottom = min(oy+h, hand_top)
            top = max(0, oy-h)
            if bottom-oy < 55:
                raise InputDeferred('combination menu overlaps the concealed hand')
            ox,oy,w,h = 0,top,image.shape[1],bottom-top
        roi = image[oy:oy+h,ox:ox+w]
        mask = cv2.inRange(cv2.cvtColor(roi,cv2.COLOR_BGR2HSV),(0,0,175),(180,55,255))
        contours,_ = cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        boxes = sorted(cv2.boundingRect(c) for c in contours
                       if 55 < cv2.boundingRect(c)[3] < 105
                       and 30 < cv2.boundingRect(c)[2] < 75
                       and cv2.contourArea(c) > np.prod(cv2.boundingRect(c)[2:])*.7)
        if any(x <= 0 or y <= 0 or x+bw >= w or y+bh >= h for x,y,bw,bh in boxes):
            raise InputDeferred('combination tile face touches the observation boundary')
        if not 2 <= len(boxes) <= 64:
            raise InputDeferred('combination tile faces not uniquely segmented')
        if self.classifier is None:
            from .tile_classifier import TileClassifier
            self.classifier = TileClassifier()
        # Menus may wrap. Sort each visible row before grouping adjacent faces;
        # global x-order would interleave the top and bottom choices.
        lines = []
        for box in sorted(boxes,key=lambda b:(b[1],b[0])):
            line = next((line for line in lines if abs(line[0][1]-box[1]) <= 8), None)
            if line is None:
                lines.append([box])
            else:
                line.append(box)
        boxes = [box for line in lines for box in sorted(line,key=lambda b:b[0])]
        predictions = self.classifier.predict([roi[y:y+bh,x:x+bw] for x,y,bw,bh in boxes])
        # This threshold is classifier probability, independent of template
        # correlation. No candidate names are supplied to the classifier.
        if any(name == 'x' or confidence < .995 for name,confidence in predictions):
            raise InputDeferred('combination glyph classification is uncertain')
        rows = []
        for (x,y,bw,bh),(name,confidence) in zip(boxes,predictions,strict=True):
            if (not rows or abs(y-rows[-1]['y']) > 8 or x-rows[-1]['right'] > bw*.3):
                rows.append({'names':[], 'x':x, 'y':y, 'right':x+bw, 'bottom':y+bh, 'confidence':confidence})
            row = rows[-1]
            if abs(y-row['y']) > 8:
                raise InputDeferred('combination faces are not aligned')
            row['names'].append(name)
            row.update(right=x+bw,bottom=max(row['bottom'],y+bh),confidence=min(row['confidence'],confidence))
        return [(row['names'],Box(round((ox+row['x'])*sx),round((oy+row['y'])*sy),
                                  round((row['right']-row['x'])*sx),round((row['bottom']-row['y'])*sy),
                                  row['confidence'],'combo_dynamic')) for row in rows]
