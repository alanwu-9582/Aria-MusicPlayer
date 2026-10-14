"""Aria MusicPlayer — run with `python main.py`."""

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()     # Aria.exe: a background worker starts here and never reaches the app

    from aria.app import main
    raise SystemExit(main())
