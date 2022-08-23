set -x
rm -rf venv
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install pytest
python -m pip install numpy
