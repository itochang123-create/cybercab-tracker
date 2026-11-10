# Cybercab detector v1
YOLOv8n (vehicles: car, motorcycle, bus, truck; conf >= 0.25, imgsz 1280) -> CLIP ViT-B-32
(`laion2b_s34b_b79k`) embedding of car crops with min side >= 24 px -> logistic regression
(`cybercab_clf.joblib`, threshold 0.8, saved with scikit-learn 1.9.1).

Training (done on the original box, not reproducible from this repository): 98 Cybercab and
1,710 not-Cybercab crops from our own frames, hand-reviewed hard negatives (Waymos, Model 3/Y,
Cybertrucks), plus Robotaxi Tracker crops used for training only and never republished.
5-fold cross-validation grouped by camera at 0.8: precision 0.86, recall 0.87.

Parked-car dedupe: see `../data/DEDUPE.md`.
