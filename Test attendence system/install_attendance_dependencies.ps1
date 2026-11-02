$ErrorActionPreference = 'Stop'

$python = if ($env:CAMPUS_PYTHON) {
    $env:CAMPUS_PYTHON
} else {
    (Get-Command python -ErrorAction Stop).Source
}

& $python -m pip install --user numpy opencv-python pandas openpyxl click Pillow
if ($LASTEXITCODE -ne 0) { throw 'Could not install the attendance imaging and spreadsheet packages.' }

# face_recognition_models still imports pkg_resources, supplied by this known-compatible setuptools release.
& $python -m pip install --user --no-deps 'setuptools==65.5.0'
if ($LASTEXITCODE -ne 0) { throw 'Could not install the attendance compatibility dependency.' }

# dlib-bin provides a Windows wheel so setup does not need a local C++ compiler.
& $python -m pip install --user --no-deps 'dlib-bin==20.0.1.post1'
if ($LASTEXITCODE -ne 0) { throw 'Could not install the Windows dlib wheel.' }

# Install face-recognition without its dlib dependency; dlib-bin supplies that module.
& $python -m pip install --user --no-deps 'face-recognition==1.3.0'
if ($LASTEXITCODE -ne 0) { throw 'Could not install face-recognition.' }
& $python -m pip install --user --no-deps 'face-recognition-models==0.3.0'
if ($LASTEXITCODE -ne 0) { throw 'Could not install the face-recognition model files.' }

& $python -c 'import cv2, face_recognition, face_recognition_models, numpy, pandas, openpyxl, dlib; print(1)'
if ($LASTEXITCODE -ne 0) { throw 'The attendance dependencies did not pass their import check.' }
