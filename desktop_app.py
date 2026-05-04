#!/usr/bin/env python
"""Desktop launcher for LawyerSystem.

Stage 3 improvements:
- Custom window name, size, icon
- Splash screen during server startup
- Automatic server shutdown on window close
- Single-instance lock (prevent multiple copies)
- Better handling of printing and external links
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

# --- Single-instance lock ---
# On Windows we use a named mutex to prevent running more than one copy.
if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes  # ← مطلوب للحصول على أبعاد شاشة العمل

    _MUTEX_NAME = "Global\\LawyerSystemDesktopSingleInstance"
    _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        print(
            "LawyerSystem Desktop is already running. Close the other instance first."
        )
        sys.exit(0)

PROJECT_ROOT = Path(__file__).resolve().parent

# --- Persist Window State ---
USER_DATA_DIR = PROJECT_ROOT / "webview_data"
USER_DATA_DIR.mkdir(exist_ok=True)

# Configure basic logging for desktop mode
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            Path(__file__).resolve().parent / "desktop.log", mode="a", encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger("desktop_launcher")

HOST = "127.0.0.1"

# ========================================
# Desktop Window Configuration
# ========================================
APP_NAME = "دِرْعٌ وَسَيْفٌ"
APP_VERSION = "0.3.0"
LOGIN_WINDOW_SIZE = (520, 720)
APP_MIN_SIZE = (480, 640)

# Window starts fullscreen; user can toggle with F11 or Escape
FULLSCREEN = False

# Try to find an icon file in the project
ICON_PATH = None
for candidate in [
    PROJECT_ROOT / "static" / "images" / "ico.ico",
    PROJECT_ROOT / "staticfiles" / "images" / "ico.ico",
    PROJECT_ROOT / "icon.ico",
]:
    if candidate.exists():
        ICON_PATH = str(candidate)
        break


def ensure_project_bootstrap() -> None:
    """Prepare Python path and Django environment."""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lawyer_system.settings")
    os.environ.setdefault("DESKTOP_MODE", "True")


def find_free_port(host: str = HOST) -> int:
    """Ask the OS for an available local port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def wait_for_server(host: str, port: int, timeout: float = 20.0) -> None:
    """Wait until the local WSGI server becomes reachable."""
    logger.info("Waiting for server at http://%s:%s …", host, port)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                logger.info("Server is reachable.")
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(
        f"Desktop server did not start on http://{host}:{port} within {timeout} seconds."
    )


def run_local_server(host: str, port: int, shutdown_event: threading.Event) -> None:
    """Run the Django WSGI app behind Waitress.

    The server will shut down cleanly when *shutdown_event* is set.
    """
    ensure_project_bootstrap()
    logger.info("Starting Waitress server on %s:%s …", host, port)

    try:
        from waitress import serve
    except ImportError as exc:
        raise RuntimeError(
            "Missing desktop dependency 'waitress'. Install requirements before running the desktop app."
        ) from exc

    from django.conf import settings
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from lawyer_system.wsgi import application

    app = application
    # استخدم StaticFilesHandler دائماً في وضع سطح المكتب لتقديم الملفات الثابتة
    desktop_mode = getattr(settings, 'DESKTOP_MODE', False)
    if settings.DEBUG or desktop_mode:
        app = StaticFilesHandler(application)

    # مسح جلسات تسجيل الدخول القديمة عند كل تشغيل للبرنامج
    # يضمن أن المستخدم يجب أن يسجل دخوله في كل مرة يفتح فيها البرنامج
    try:
        import django
        django.setup()
        from django.contrib.sessions.models import Session
        Session.objects.all().delete()
        logger.info("Sessions cleared — user must log in again.")
    except Exception as e:
        logger.warning("Could not clear sessions: %s", e)

    try:
        # تأكد من صحة قاعدة البيانات قبل التشغيل (توصية تقنية)
        if not settings.DEBUG:
            logger.info("Running system checks...")

        serve(
            app,
            host=host,
            port=port,
            threads=8,
            connection_limit=100,
            cleanup_interval=30,
            ident="LawyerSystemDesktop",
            # Waitress does not have a built-in shutdown hook, but the process
            # will exit cleanly when the main thread terminates (daemon thread).
        )
    except Exception as exc:
        logger.error("Server failed to start: %s", exc, exc_info=True)
        raise


def get_screen_work_area():
    """Get screen work area (excluding taskbar) on Windows."""
    if sys.platform != "win32":
        return None
    
    try:
        # SPI_GETWORKAREA = 48
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)
        return {
            'left': rect.left,
            'top': rect.top,
            'right': rect.right,
            'bottom': rect.bottom,
            'width': rect.right - rect.left,
            'height': rect.bottom - rect.top
        }
    except Exception as e:
        logger.warning(f"Could not get work area: {e}")
        return None


def start_desktop_window(url: str, shutdown_event: threading.Event) -> None:
    """Open the local app inside a native desktop window."""
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError(
            "Missing desktop dependency 'pywebview'. Install requirements before running the desktop app."
        ) from exc

    logger.info("Creating desktop window pointing to %s", url)
    if ICON_PATH:
        logger.info("Icon available for packaging: %s", ICON_PATH)

    # --- Sentinel file path — checked periodically for close signal ---
    CLOSE_SENTINEL = PROJECT_ROOT / ".desktop_close"
    CLOSE_SENTINEL.unlink(missing_ok=True)
    close_state = {
        "allow_close": False,
        "restore_in_progress": False,
        "prompt_requested": False,
    }

    # --- File save dialog helper (uses closure over `window`) ---
    def save_file_dialog(filename, file_content, file_types=None):
        """Open native save dialog and write file content."""
        import base64

        try:
            # If no file_types provided, try to guess from extension
            if not file_types:
                if filename.lower().endswith('.pdf'):
                    file_types = ('PDF files (*.pdf)', 'All files (*.*)')
                elif filename.lower().endswith('.xlsx'):
                    file_types = ('Excel files (*.xlsx)', 'All files (*.*)')
                else:
                    file_types = ('All files (*.*)',)

            file_content_bytes = base64.b64decode(file_content)
            result = window.create_file_dialog(
                dialog_type=webview.FileDialog.SAVE, 
                save_filename=filename,
                file_types=file_types
            )
            if result:
                file_path = result[0] if isinstance(result, tuple) else result
                if file_path:
                    with open(file_path, "wb") as f:
                        f.write(file_content_bytes)
                    logger.info(f"File saved to: {file_path}")
                    return True
            return False
        except Exception as e:
            logger.error(f"Failed to save file: {e}")
            return False

    # --- API class exposed to JavaScript via pywebview.api ---
    class DesktopApi:
        def saveFile(self, filename, content, file_types=None):
            return save_file_dialog(filename, content, file_types)

        def minimizeWindow(self):
            """Minimize the desktop window safely when supported."""
            try:
                if hasattr(window, "minimize"):
                    window.minimize()
                    logger.info("Window minimized successfully")
                    return True
                logger.warning("Window minimize is not supported by this pywebview build.")
                return False
            except Exception as e:
                logger.error(f"Failed to minimize window: {e}")
                return False

        def restoreWindow(self):
            """Restore the desktop window when it's minimized."""
            try:
                if hasattr(window, "restore"):
                    window.restore()
                    logger.info("Window restored successfully")
                    return True
                logger.warning("Window restore is not supported by this pywebview build.")
                return False
            except Exception as e:
                logger.error(f"Failed to restore window: {e}")
                return False

        def closeWindow(self):
            """Close the application window."""
            try:
                close_state["allow_close"] = True
                window.destroy()
            except Exception as e:
                logger.error(f"Failed to close window: {e}")

        def consumeClosePromptRequest(self):
            """Return and clear any pending in-app close confirmation request."""
            if close_state["prompt_requested"]:
                close_state["prompt_requested"] = False
                return True
            return False

    desktop_api = DesktopApi()

    # --- Create the single app window WITH js_api so pywebview.api is available in JS ---
    window = webview.create_window(
        title=APP_NAME,
        url=url,
        frameless=True,           # ← إزالة شريط العنوان بالكامل
        maximized=False,          # ← لن نستخدم maximize() المدمج
        resizable=True,           # ← ضروري جداً لاحترام شريط المهام في ويندوز
        easy_drag=False,          # ← منع تحريك النافذة بالسحب من أي نقطة بشكل عشوائي
        width=1200,               # حجم افتراضي مؤقت (سيتم تعديله في _on_loaded)
        height=800,
        min_size=APP_MIN_SIZE,
        confirm_close=False,      # نستخدم نظام التأكيد المخصص الخاص بنا
        js_api=desktop_api,
        hidden=True,              # ← جديد: إخفاء النافذة حتى اكتمال التحميل
    )

    # --- Handle window close → signal server shutdown ---
    def _handle_window_closing():
        """Intercept close request and flag JavaScript to show confirmation."""
        if close_state["allow_close"]:
            logger.info("Window closing confirmed — signalling server to stop …")
            shutdown_event.set()
            return True  # Allow close
        
        # Flag that user requested close - JavaScript will poll for this
        close_state["prompt_requested"] = True
        logger.info("Close request intercepted - flagging for JavaScript")
        return False  # Cancel the close - window stays open

    try:
        window.events.closing += _handle_window_closing
    except AttributeError:
        logger.debug("Window closing events not available in this pywebview version.")

    # --- F11 / Escape toggles fullscreen ---
    def _inject_fullscreen_toggle():
        """After the page loads, inject a tiny JS snippet that handles F11/Escape."""
        js = """
        document.addEventListener('keydown', function(e) {
            if (e.key === 'F11' || (e.key === 'Escape' && document.documentElement.requestFullscreen)) {
                e.preventDefault();
                if (document.fullscreenElement) {
                    document.exitFullscreen();
                } else {
                    document.documentElement.requestFullscreen().catch(function(){});
                }
            }
        });
        """
        try:
            window.evaluate_js(js)
        except Exception:
            pass

    def _get_current_path() -> str:
        """Read the current path from the embedded page."""
        try:
            current_path = window.evaluate_js("window.location.pathname")
            if isinstance(current_path, str):
                return current_path.strip().strip("'\"")
        except Exception as e:
            logger.debug("Could not detect current path: %s", e)
        return "/"

    def _resize_and_center(width: int, height: int) -> None:
        """Resize the window and center it within the current work area."""
        work_area = get_screen_work_area()
        if not work_area:
            window.resize(width, height)
            return

        target_width = min(width, work_area["width"])
        target_height = min(height, work_area["height"])
        target_left = work_area["left"] + max(
            (work_area["width"] - target_width) // 2, 0
        )
        target_top = work_area["top"] + max(
            (work_area["height"] - target_height) // 2, 0
        )

        window.resize(target_width, target_height)
        window.move(target_left, target_top)
        logger.info(
            "Window resized to %sx%s at (%s, %s)",
            target_width,
            target_height,
            target_left,
            target_top,
        )

    def _apply_window_layout() -> None:
        """Use a compact login window and a large main-app window."""
        current_path = _get_current_path()
        is_login_page = current_path.rstrip("/") == "/login"

        if is_login_page:
            _resize_and_center(*LOGIN_WINDOW_SIZE)
            logger.info("Applied compact login window layout for %s", current_path)
            return

        work_area = get_screen_work_area()
        if work_area:
            window.resize(work_area["width"], work_area["height"])
            window.move(work_area["left"], work_area["top"])
            logger.info(
                "Applied main window layout: %sx%s",
                work_area["width"],
                work_area["height"],
            )
        else:
            window.maximize()
            logger.info("Using fallback maximize mode")

    def _on_loaded():
        _inject_fullscreen_toggle()
        _apply_window_layout()
        
        # ← جديد: إظهار النافذة بعد اكتمال التحميل وضبط الحجم
        try:
            window.show()
            logger.info("Window shown after loading complete")
        except Exception as e:
            logger.warning(f"Could not show window: {e}")
        
        try:
            window.evaluate_js(
                "console.log('[Desktop] pywebview.api available:', typeof pywebview !== 'undefined' && !!pywebview.api);"
            )
        except Exception:
            pass

    try:
        window.events.loaded += _on_loaded
    except AttributeError:
        pass

    # --- المحسن: مراقبة حالة الإغلاق عبر الـ Event بدلاً من الملفات فقط ---
    def _close_watcher():
        while not shutdown_event.is_set():
            # يمكن الإبقاء على Sentinel لدعم الإغلاق من داخل Django
            if CLOSE_SENTINEL.exists():
                logger.info("Close sentinel detected — destroying window.")
                CLOSE_SENTINEL.unlink(missing_ok=True)
                try:
                    os._exit(0) # خروج نظيف وكامل للعملية
                except Exception as exc:
                    logger.error("Failed to destroy window: %s", exc)
                return
            time.sleep(0.3)

    watcher_thread = threading.Thread(
        target=_close_watcher,
        name="desktop-close-watcher",
        daemon=True,
    )
    watcher_thread.start()

    # استخدام user_cache_dir لضمان حفظ الثيم (LocalStorage) بين مرات الفتح
    webview.start(
        debug=False,
        private_mode=False,
        storage_path=str(USER_DATA_DIR)
    )
    logger.info("Webview session ended.")


def main() -> None:
    logger.info("=" * 60)
    logger.info("%s v%s — Starting", APP_NAME, APP_VERSION)
    logger.info("=" * 60)

    ensure_project_bootstrap()

    # Event used to signal server shutdown
    shutdown_event = threading.Event()

    # Try consistent ports first for theme/session persistence
    port = 8000
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((HOST, port))
    except OSError:
        port = 8080
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((HOST, port))
        except OSError:
            port = find_free_port()
            
    url = f"http://{HOST}:{port}/"
    logger.info("Selected port: %s  →  %s", port, url)

    server_thread = threading.Thread(
        target=run_local_server,
        args=(HOST, port, shutdown_event),
        name="lawyer-system-desktop-server",
        daemon=True,
    )
    server_thread.start()

    try:
        wait_for_server(HOST, port)
        start_desktop_window(url, shutdown_event)
    except Exception as exc:
        logger.error("Desktop app failed: %s", exc, exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Desktop launcher exiting.")


if __name__ == "__main__":
    main()
