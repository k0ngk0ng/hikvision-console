# Third-party components

The application source is GPL-3.0-or-later. Binary distributions must retain these notices and the licenses/source availability of bundled components.

| Component | License / upstream source |
| --- | --- |
| Qt / PySide6 | LGPL-3.0 / GPL-3.0 / commercial; https://code.qt.io/pyside/pyside-setup.git and https://code.qt.io/qt/ |
| libVLC and VLC plugins | LGPL-2.1-or-later / GPL-2.0-or-later depending on component; https://code.videolan.org/videolan/vlc |
| python-vlc | LGPL-2.1-or-later; https://github.com/oaubert/python-vlc |
| FFmpeg (imageio binary) | Build-dependent GPL/LGPL; inspect bundled `ffmpeg -L` and `ffmpeg -version`; https://ffmpeg.org/download.html and https://github.com/imageio/imageio-ffmpeg |
| imageio-ffmpeg | BSD-2-Clause; https://github.com/imageio/imageio-ffmpeg |
| httpx / httpcore | BSD-3-Clause; https://github.com/encode/httpx and https://github.com/encode/httpcore |
| defusedxml | PSF license; https://github.com/tiran/defusedxml |
| Python | PSF license; https://www.python.org/downloads/source/ |

The packaging script reads the existing target-platform VLC installation. Before redistributing a binary, include the matching VLC/FFmpeg source and applicable component license texts or satisfy their corresponding-source requirements through your distribution channel. The private local test build is not a signed public release.
