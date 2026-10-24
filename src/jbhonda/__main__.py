import sys


def main() -> None:
    from jbhonda.ui.app import run

    sys.exit(run(smoke_test="--smoke-test" in sys.argv))


if __name__ == "__main__":
    main()
