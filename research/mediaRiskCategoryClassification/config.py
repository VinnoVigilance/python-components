"""Fixed experiment scope; change the dataset version when changing labels or input policy."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / 'data'
OUTPUT_DIR = ROOT / 'outputs'
SEED = 42
LABELS = (
    'Terrorism / Terrorist Financing',
    'Regulatory',
    'Law Enforcement',
    'Judicial / Court',
    'Crime-Corruption',
    'Crime-Fraud',
    'Crime-Forgery & Counterfeiting',
    'Crime-Cybercrime',
    'Crime-Trafficking & Smuggling',
    'Crime-Violent Crime',
    'Crime-Environmental & Wildlife Crime',
)
