import re


class FakeIMAPConnection:
    """Minimal in-memory double for the tiny imaplib surface webde_imap actually uses.

    Only implements .status(), .select(), .uid("SEARCH"/"FETCH"), .logout() --
    exactly what src/webde_imap/imap_client.py calls. No real network involved.
    """

    def __init__(self, messages=None, uidvalidity=1000):
        # uid -> raw RFC822 bytes
        self.messages = dict(messages or {})
        self.uidvalidity = uidvalidity
        self.selected_folder = None

    def add_message(self, uid, raw_bytes):
        self.messages[uid] = raw_bytes

    def status(self, folder, what):  # noqa: ARG002 -- `what` is always "(UIDVALIDITY)" here
        return "OK", [f"{folder} (UIDVALIDITY {self.uidvalidity})".encode()]

    def select(self, folder, readonly=True):  # noqa: ARG002
        self.selected_folder = folder
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        command = command.upper()
        if command == "SEARCH":
            criteria = args[-1]
            return "OK", [" ".join(str(u) for u in self._search(criteria)).encode()]
        if command == "FETCH":
            uid = int(args[0])
            raw = self.messages.get(uid)
            if raw is None:
                return "NO", [None]
            return "OK", [(f"{uid} (RFC822 {{{len(raw)}}}".encode(), raw)]
        raise NotImplementedError(command)

    def _search(self, criteria):
        uids = sorted(self.messages.keys())
        if criteria == "ALL":
            return uids
        match = re.search(r"UID (\d+):\*", criteria)
        if match:
            start = int(match.group(1))
            return [uid for uid in uids if uid >= start]
        if "SINCE" in criteria:
            return uids
        return uids

    def logout(self):
        pass
