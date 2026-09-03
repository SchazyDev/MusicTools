from PyQt5.QtCore import QThread, pyqtSignal
import yt_dlp

class PreviewThread(QThread):
    preview_ready = pyqtSignal(str, str, list)
    error = pyqtSignal(str)


    def __init__(self, url):
        super().__init__()
        self.url = url


    def run(self):
        try:
            ydl_opts = {
                'quiet': True,
                'skip_download': True,
                'no_warnings': True,
                'forcejson': True
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                title = info.get('title', 'Untitled')
                thumbnail = info.get('thumbnail', '')
                heights = []

                if 'formats' in info:
                    for f in info['formats']:
                        h = f.get('height')
                        
                        if (h is not None and h >= 144
                            and f.get('vcodec') != 'none'):
                                heights.append(int(h))

                heights = sorted(set(heights), reverse=True)
                self.preview_ready.emit(title, thumbnail, heights)

        except Exception as e:
            self.error.emit(str(e))