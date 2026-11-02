import time
import sys

try:
    import pythoncom
    import win32com.client
except Exception as e:
    print(f"COM imports failed: {e}")
    sys.exit(2)


def main():
    try:
        pythoncom.CoInitialize()
        ppt_app = win32com.client.GetActiveObject("PowerPoint.Application")
    except Exception as e:
        print("Could not get PowerPoint ActiveObject:", e)
        sys.exit(1)

    try:
        if ppt_app.Presentations.Count == 0:
            print("No active presentation open")
            sys.exit(1)
        pres = ppt_app.ActivePresentation
        try:
            ssw = pres.SlideShowWindow
            ssw.View.Next()
            print("Advanced slide via SlideShowWindow")
            sys.exit(0)
        except Exception:
            # Not in slideshow, try to start it then advance
            try:
                settings = pres.SlideShowSettings
                settings.ShowPresenterView = False
                ssr = settings.Run()
                time.sleep(0.5)
                try:
                    pres.SlideShowWindow.View.Next()
                    print("Started show and advanced slide")
                    sys.exit(0)
                except Exception as e2:
                    print("Started show but failed to advance:", e2)
                    sys.exit(1)
            except Exception as e3:
                print("Failed to start slide show:", e3)
                sys.exit(1)

    except Exception as e:
        print("COM error during presentation handling:", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
