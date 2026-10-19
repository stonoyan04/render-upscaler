"""`python -m render_upscaler` opens the app; `python -m render_upscaler cli ...` runs the command line."""

import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        from .cli import main as cli_main

        return cli_main(sys.argv[2:])
    from .gui import main as gui_main

    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
