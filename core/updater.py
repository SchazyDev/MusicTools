import os
import urllib.request
import tempfile
import subprocess
import json

from utils.constants import VERSION, GITHUB_REPO
from utils.logger import setup_logger

logger = setup_logger(__name__)


def check_for_updates():
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.loads(response.read().decode())
            tag = data.get('tag_name', '')

            if tag.startswith('v'):
                tag = tag[1:]

            assets = data.get('assets', [])
            download_url = None

            for asset in assets:
                name = asset.get('name', '')

                if name.lower().endswith('.exe'):
                    download_url = asset.get('browser_download_url')
                    break

            if not download_url:
                download_url = data.get('html_url', '')

            if tag and download_url and tag > VERSION:
                return tag, download_url
            
    except Exception as e:
        logger.warning(f"GitHub update check failed: {e}")

    return None, None


def download_file(url, dest_path):
    try:
        urllib.request.urlretrieve(url, dest_path)
        return True
    
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return False


def create_update_script(current_exe, new_exe):
    script = f'''@echo off
:retry
move /Y "{new_exe}" "{current_exe}"
if errorlevel 1 (
    timeout /t 2 /nobreak >nul
    goto retry
)
start "" "{current_exe}"
del "%~f0"
'''
    script_path = os.path.join(tempfile.gettempdir(), 'update_script.bat')
    with open(script_path, 'w', encoding='utf-8') as f:
        f.write(script)

    return script_path


def perform_update(download_url, current_exe_path):
    temp_dir = tempfile.mkdtemp(prefix='fauna_update_')
    new_exe_path = os.path.join(temp_dir, os.path.basename(current_exe_path))

    if not download_file(download_url, new_exe_path):
        logger.error("Failed to download update file")
        return False

    script_path = create_update_script(current_exe_path, new_exe_path)
    subprocess.Popen([script_path], shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return True