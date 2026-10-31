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

    def profile(self):
        if not self.path.exists():
            raise InputDeferred("WebGL screenshot calibration is missing")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def normalized(self,raw,profile):
        image=decode_image(raw)
        height,width=image.shape[:2]
        expected=profile["width"]/profile["height"]
        if abs(width/height/expected-1)>.01:
            raise InputDeferred("viewport aspect ratio changed; recalibration required")
        return cv2.resize(image,(profile["width"],profile["height"])),width/profile["width"],height/profile["height"]

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

    def matches(self, raw, labels, *, region=None):
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
            patch = decode_image((self.folder/template["file"]).read_bytes())
            ph,pw = patch.shape[:2]
            if search.shape[0] < ph or search.shape[1] < pw:
                continue
            if float(np.std(patch)) < 3:
                raise InputDeferred("calibration patch has no identifying detail")
            result = cv2.matchTemplate(search,patch,cv2.TM_CCOEFF_NORMED)
            # Suppress adjacent pixel maxima for one physical tile/button.
            for _ in range(20):
                _,value,_,point = cv2.minMaxLoc(result)
                if value < profile.get("threshold",.94):
                    break
                x,y = point
                boxes.append(Box(round((x+ox)*sx),round((y+oy)*sy),round(pw*sx),round(ph*sy),value,label))
                result[max(0,y-ph//2):y+ph//2+1,max(0,x-pw//2):x+pw//2+1] = -1
        return sorted(boxes,key=lambda b:(b.x,b.y,b.label))

    def hand(self, raw, state):
        if state.public_events and state.public_events[-1]['kind'] == 'ActionNewRound':
            # Also accept pre-fix cached snapshots: initial dealing is a sorted
            # complete row, not an ActionDealTile draw instance.
            state = state.model_copy(update={'drawn_index':None})
        profile = self.profile()
        if "hand_region" not in profile:
            raise InputDeferred("hand region not calibrated")
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
        found = self.matches(raw, ['tile_'+tile], region=region)
        if not found:
            raise InputDeferred('legal riichi discard glyph is not visible')
        found.sort(key=lambda b:b.x)
        if state.drawn_index == index:
            return found[-1]
        same = [i for i,t in enumerate(state.hand) if t == tile and i != state.drawn_index]
        occurrence = same.index(index)
        if occurrence >= len(found):
            raise InputDeferred('riichi tile instance is not visible')
        return found[occurrence]

    def combo(self, raw, kind, tiles, offered=None):
        # Crops identify the complete offered combination including red tiles.
        label = "combo_"+kind+"_"+"_".join(sorted(tiles))
        if label in self.profile()['templates']:
            found = self.matches(raw,[label])
            if len(found) == 1:
                return found[0]
        groups = self.menu_groups(raw)
        actual = [tuple(sorted(names)) for names,_ in groups]
        if offered is not None and (set(actual) != {tuple(sorted(t)) for t in offered}
                                    or len(actual) != len(set(actual))):
            raise InputDeferred('visible menu differs from complete server choices')
        found = [box for names,box in groups if sorted(names) == sorted(tiles)]
        if len(found) != 1:
            raise InputDeferred('selected meld combination is not uniquely visible')
        return found[0]

    def menu_groups(self, raw):
        profile = self.profile()
        if 'combo_region' not in profile:
            raise InputDeferred('combination menu region not calibrated')
        image,sx,sy = self.normalized(raw,profile)
        ox,oy,w,h = profile['combo_region']
        roi = image[oy:oy+h,ox:ox+w]
        mask = cv2.inRange(cv2.cvtColor(roi,cv2.COLOR_BGR2HSV),(0,0,175),(180,55,255))
        contours,_ = cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        boxes = sorted(cv2.boundingRect(c) for c in contours
                       if 55 < cv2.boundingRect(c)[3] < 105
                       and 30 < cv2.boundingRect(c)[2] < 75
                       and cv2.contourArea(c) > np.prod(cv2.boundingRect(c)[2:])*.7)
        if not 2 <= len(boxes) <= 16:
            raise InputDeferred('combination tile faces not uniquely segmented')
        if self.classifier is None:
            from .tile_classifier import TileClassifier
            self.classifier = TileClassifier()
        predictions = self.classifier.predict([roi[y:y+bh,x:x+bw] for x,y,bw,bh in boxes])
        # This threshold is classifier probability, independent of template
        # correlation. No candidate names are supplied to the classifier.
        if any(name == 'x' or confidence < .995 for name,confidence in predictions):
            raise InputDeferred('combination glyph classification is uncertain')
        rows = []
        for (x,y,bw,bh),(name,confidence) in zip(boxes,predictions,strict=True):
            if (not rows or x-rows[-1]['right'] > bw*.3):
                rows.append({'names':[], 'x':x, 'y':y, 'right':x+bw, 'bottom':y+bh, 'confidence':confidence})
            row = rows[-1]
            if abs(y-row['y']) > 8:
                raise InputDeferred('combination faces are not aligned')
            row['names'].append(name)
            row.update(right=x+bw,bottom=max(row['bottom'],y+bh),confidence=min(row['confidence'],confidence))
        return [(row['names'],Box(round((ox+row['x'])*sx),round((oy+row['y'])*sy),
                                  round((row['right']-row['x'])*sx),round((row['bottom']-row['y'])*sy),
                                  row['confidence'],'combo_dynamic')) for row in rows]
