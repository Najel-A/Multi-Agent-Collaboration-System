# Capstone Training Pipeline (najel-data/training)

This folder contains a **modular, config-driven** training workflow for incident datasets stored as **JSONL** with a shared schema across teammates.

## Layout

- `notebooks/`: thin orchestration notebooks (data prep, train, eval)
- `src/capstone_training/`: reusable Python modules (load/validate/build/train/eval)
- `artifacts/`: outputs (processed datasets, trained models, reports)
- `config/`: optional configs (YAML/JSON) you can add later

## Quick start

Create / activate your env from repo root, then:

```bash
python3 -m pip install -r najel-data/training/requirements.txt
```

In notebooks, we add `najel-data/training/src` to `sys.path` so imports work without packaging.

