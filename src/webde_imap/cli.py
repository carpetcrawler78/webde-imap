import argparse
import logging
import sys

from . import config as config_mod
from . import imap_client
from . import runner
from . import state as state_mod


def build_arg_parser():
    parser = argparse.ArgumentParser(description="WEB.DE -> Gmail Forwarding Bridge")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur klassifizieren, nichts per SMTP senden, State nicht speichern.",
    )
    parser.add_argument(
        "--bootstrap-last-n",
        type=int,
        default=None,
        help="Nur fuer workflow_dispatch: importiere die letzten N Mails.",
    )
    parser.add_argument(
        "--bootstrap-window-hours",
        type=int,
        default=None,
        help="Nur fuer workflow_dispatch: importiere Mails der letzten N Stunden.",
    )
    parser.add_argument(
        "--state-path",
        default=None,
        help="Ueberschreibt den Pfad zu runtime/webde_state.json (z.B. fuer lokale Tests).",
    )
    return parser


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_arg_parser().parse_args(argv)

    config = config_mod.load_config()
    if not args.dry_run:
        config_mod.require_smtp_config(config)
    rules = config_mod.load_routing_rules()

    state_path = args.state_path or state_mod.DEFAULT_STATE_PATH

    mail = imap_client.connect(config)
    try:
        result = runner.run(
            mail,
            config,
            rules,
            state_path,
            dry_run=args.dry_run,
            bootstrap_last_n=args.bootstrap_last_n,
            bootstrap_window_hours=args.bootstrap_window_hours,
        )
    finally:
        imap_client.logout(mail)

    print(
        f"processed={result.total_processed} forwarded={result.forwarded_count} "
        f"ignored={result.ignored_count} had_failure={result.had_failure}"
    )
    for folder_result in result.folder_results:
        print(
            f"  folder={folder_result.folder} processed={len(folder_result.processed_uids)} "
            f"forwarded={folder_result.forwarded_count} ignored={folder_result.ignored_count} "
            f"had_failure={folder_result.had_failure}"
        )

    return 1 if result.had_failure else 0


if __name__ == "__main__":
    sys.exit(main())
