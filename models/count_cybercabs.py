#!/usr/bin/env python3
"""Count vehicles and Cybercabs per frame in /workspace/cybercab/frames.
Stage 1 (cached per frame in data/det_cache/<cycle>/<cam>.npz): YOLOv8n vehicle boxes
(car/motorcycle/bus/truck, conf>=0.25, imgsz 1280) + CLIP ViT-B-32 embeddings of car crops
with min side >= MIN_SIDE px. Stage 2: logistic-regression Cybercab classifier
(models/cybercab_clf.joblib) on the embeddings; crops scoring >= threshold count as Cybercabs.
Writes data/own_counts.csv. Usage: count_cybercabs.py [--no-classify]"""
import os, sys, json, glob, csv, numpy as np, torch
from PIL import Image
R=os.environ.get('CYBERCAB_ROOT','/workspace/cybercab'); CACHE=f'{R}/data/det_cache'; MIN_SIDE=24
VEH={2:'car',3:'motorcycle',5:'bus',7:'truck'}
torch.set_num_threads(int(os.environ.get('CYBERCAB_TORCH_THREADS') or os.cpu_count() or 4))
_m={}
def models():
    if not _m:
        from ultralytics import YOLO; import open_clip
        _m['yolo']=YOLO(f'{R}/models/yolov8n.pt')
        m,_,p=open_clip.create_model_and_transforms('ViT-B-32',pretrained='laion2b_s34b_b79k'); m.eval()
        _m['clip']=(m,p)
    return _m
def embed(imgs):
    m,p=models()['clip']; out=[]
    with torch.no_grad():
        for i in range(0,len(imgs),64):
            x=torch.stack([p(im) for im in imgs[i:i+64]])
            e=m.encode_image(x); out.append((e/e.norm(dim=-1,keepdim=True)).numpy())
    return np.concatenate(out) if out else np.zeros((0,512),np.float32)
def crop(im,b,pad=0.1):
    x1,y1,x2,y2=b; w,h=x2-x1,y2-y1
    return im.crop((max(0,x1-pad*w),max(0,y1-pad*h),min(im.width,x2+pad*w),min(im.height,y2+pad*h)))
def process(jpg):
    cyc=jpg.split('/')[-2]; cam=os.path.basename(jpg)[:-4]
    out=f'{CACHE}/{cyc}/{cam}.npz'
    if os.path.exists(out): return out
    os.makedirs(os.path.dirname(out),exist_ok=True)
    im=Image.open(jpg).convert('RGB')
    r=models()['yolo'](im,imgsz=1280,conf=0.25,classes=list(VEH),verbose=False)[0]
    b=r.boxes.xyxy.numpy(); c=r.boxes.cls.numpy().astype(int); s=r.boxes.conf.numpy()
    big=np.where((c==2)&(np.minimum(b[:,2]-b[:,0],b[:,3]-b[:,1])>=MIN_SIDE))[0]
    E=embed([crop(im,b[i]) for i in big])
    np.savez(out,boxes=b,cls=c,conf=s,emb_idx=big,emb=E.astype(np.float16))
    return out
def main():
    frames=sorted(glob.glob(f'{R}/frames/*/*.jpg'))
    clf=None
    if '--no-classify' not in sys.argv:
        import joblib; clf=joblib.load(f'{R}/models/cybercab_clf.joblib')
    rows=[]
    for k,j in enumerate(frames):
        try: z=np.load(process(j))
        except Exception as e: print('ERR',j,e); continue
        meta=json.load(open(j[:-4]+'.json')) if os.path.exists(j[:-4]+'.json') else {}
        n_cc=0
        if clf and len(z['emb']):
            p=clf['model'].predict_proba(z['emb'].astype(np.float32))[:,1]; n_cc=int((p>=clf['threshold']).sum())
        rows.append(dict(cycle_id=j.split('/')[-2],camera_id=os.path.basename(j)[:-4],
            fetched_at=meta.get('fetched_at',''),series_id=meta.get('series_id',''),
            vehicles=len(z['cls']),cars_classified=len(z['emb']),cybercabs=n_cc))
        if k%200==0: print(k,len(frames),flush=True)
    with open(f'{R}/data/own_counts.csv','w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print('wrote',len(rows))
if __name__=='__main__': main()
