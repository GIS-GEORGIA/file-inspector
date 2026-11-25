import sys

if __name__ == "__main__":      # importing this module must not start the app
    from .app import main
    sys.exit(main())
