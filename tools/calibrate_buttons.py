"""Validate local button glyph crops and optionally merge an idle calibration.

The manifest and screenshots stay private. Images are scaled locally by width,
then bottom-cropped/padded to the calibration canvas for glyph tests only. This
does not claim old screenshots have the current live viewport geometry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import cv2
import httpx
import numpy as np

from majsoul_llm.config import Settings
from majsoul_llm.locator import ScreenLocator, decode_image
from majsoul_llm.native import InputDeferred
from majsoul_llm.store import utcnow

KINDS = {'chi','pon','riichi','ron','tsumo','pass','ankan','kakan','daiminkan','abort9'}


def image_at_width(path, width):
    image = decode_image(path.read_bytes())
    return cv2.resize(image, (width, round(image.shape[0]*width/image.shape[1])))


def png(image):
    return cv2.imencode('.png', image)[1].tobytes()


def validate(manifest, folder):
    width, height = manifest['width'], manifest['height']
    profile = {'version':'screen.v1','width':width,'height':height,
               'threshold':manifest.get('threshold', .94),'templates':{}}
    folder.mkdir(parents=True)
    for crop in manifest['crops']:
        kind, path = crop['kind'], Path(crop['source'])
        if kind not in KINDS or crop['provenance'] != 'local_real_capture':
            raise ValueError('only explicitly identified real local button crops are accepted')
        original = decode_image(path.read_bytes())
        scale = width/original.shape[1]
        x,y,w,h = crop['box']
        if not (0<=x<x+w<=original.shape[1] and 0<=y<y+h<=original.shape[0]):
            raise ValueError('source crop outside image')
        image = image_at_width(path, width)
        x,y,w,h = (round(v*scale) for v in (x,y,w,h))
        patch = image[y:y+h,x:x+w]
        if min(patch.shape[:2]) < 8:
            raise ValueError('crop too small')
        label = 'button_'+kind
        filename = label+'-'+hashlib.sha256(png(patch)).hexdigest()[:12]+'.png'
        (folder/filename).write_bytes(png(patch))
        entry = {'file':filename,'size':[w,h], 'source':'local_real_capture',
                 'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'source_box':crop['box']}
        if label in profile['templates']:
            profile['templates'][label].setdefault('variants', []).append(entry)
        else:
            profile['templates'][label] = entry
    (folder/'profile.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    locator = ScreenLocator(folder.parent)
    cases = []
    crop_sources = {str(Path(c['source']).resolve()) for c in manifest['crops']}
    for case in manifest['cases']:
        path = Path(case['source'])
        scaled = image_at_width(path, width)
        canvas = np.zeros((height,width,3), dtype=np.uint8)
        canvas[:min(height,scaled.shape[0])] = scaled[:height]
        raw = png(canvas)
        found, ambiguous = {}, []
        for label in profile['templates']:
            boxes = locator.matches(raw, [label])
            if boxes:
                found[label[7:]] = [{'center':b.center,'confidence':b.confidence} for b in boxes]
                if len(boxes) != 1:
                    ambiguous.append(label)
        expected = set(case['expected'])
        passed = set(found)==expected and not ambiguous
        cases.append({'source_name':path.name, 'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'kind':'same_source_crop_check' if str(path.resolve()) in crop_sources else 'independent_capture',
            'expected':sorted(expected),'found':found,'ambiguous':ambiguous,'passed':passed})
    return profile, cases


def install(folder, profile):
    settings = Settings.load()
    with httpx.Client(trust_env=False, timeout=10) as client:
        response = client.get(f'http://127.0.0.1:{settings.port}/control/status', headers={
            'Authorization':'Bearer '+settings.control_token.get_secret_value()})
        response.raise_for_status()
        status = response.json()
    nav = status['navigation']
    if status['enabled'] or nav['active_command'] or nav.get('input_owner') or status['result_flow']['active']:
        raise InputDeferred('production input must be idle before calibration install')
    target = settings.runtime/'calibration'
    current = json.loads((target/'profile.json').read_text(encoding='utf8'))
    if (current['width'],current['height']) != (profile['width'],profile['height']):
        raise ValueError('production calibration size differs')
    backup = target/('profile.before-buttons-'+utcnow().replace(':','').replace('.','')+'.json')
    shutil.copy2(target/'profile.json', backup)
    for label, entry in profile['templates'].items():
        for item in [entry]+entry.get('variants', []):
            shutil.copy2(folder/item['file'], target/item['file'])
        if label in current['templates']:
            current['templates'][label].setdefault('variants', []).append(entry)
        else:
            current['templates'][label] = entry
    temporary = target/'profile.buttons.pending.json'
    temporary.write_text(json.dumps(current,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    temporary.replace(target/'profile.json')
    return str(backup)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf8'))
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)/'calibration'
        profile, cases = validate(manifest, folder)
        report = {'schema_version':'button-calibration.v1','at':utcnow(),'cases':cases,
            'passed':bool(cases) and all(c['passed'] for c in cases), 'installed':False,
            'source_transform':'local preserve-width resize, bottom crop/pad; glyph tests only',
            'browser_geometry_changes':0,'mouse_inputs':0}
        if args.install and report['passed']:
            report['backup'] = install(folder, profile)
            report['installed'] = True
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(report,ensure_ascii=False))
    raise SystemExit(0 if report['passed'] else 1)
