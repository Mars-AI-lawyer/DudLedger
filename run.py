import sys


def main():
    if sys.argv[1:2] == ["--agent-import"]:
        from ledger_viewer.agent_import import main as agent_main
        return agent_main(sys.argv[2:])
    from ledger_viewer.ui import main as ui_main
    ui_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
