from aria.app import main

if __name__ == "__main__":         # also keeps worker processes (spawned) from starting a second Aria
    raise SystemExit(main())
