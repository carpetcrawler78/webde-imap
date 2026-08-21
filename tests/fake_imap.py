import re


class FakeIMAPConnection:
    """Minimal in-memory double for the tiny imaplib surface webde_imap actually uses.

    Only implements .status(), .select(), .uid("SEARCH"/"FETCH"), .logout() --
    exactly what src/webde_imap/imap_client.py calls. No real network involved.
    Supports multiple folders, each with its own messages and UIDVALIDITY.
    """

    def __init__(self, messages=None, uidvalidity=1000):
        # folder -> {uid: raw RFC822 bytes}
        self.messages = {folder: dict(msgs) for folder, msgs in (messages or {}).items()}
        self.default_uidvalidity = uidvalidity
        self.folder_uidvalidity = {}
        self.selected_folder = None

    def add_message(self, folder, uid, raw_bytes):
        self.messages.setdefault(folder, {})[uid] = raw_bytes

    def set_uidvalidity(self, folder, value):
        self.folder_uidvalidity[folder] = value

    def _uidvalidity_for(self, folder):
        return self.folder_uidvalidity.get(folder, self.default_uidvalidity)

    @staticmethod
    def _unquote(folder):
        return folder[1:-1] if folder.startswith('"') and folder.endswith('"') else folder

    def status(self, folder, what):  # noqa: ARG002 -- `what` is always "(UIDVALIDITY)" here
        folder = self._unquote(folder)
        return "OK", [f"{folder} (UIDVALIDITY {self._uidvalidity_for(folder)})".encode()]

    def select(self, folder, readonly=True):  # noqa: ARG002
        self.selected_folder = self._unquote(folder)
        count = len(self.messages.get(self.selected_folder, {}))
        return "OK", [str(count).encode()]

    def uid(self, command, *args):
        command = command.upper()
        if command == "SEARCH":
            criteria = args[-1]
            return "OK", [" ".join(str(u) for u in self._search(criteria)).encode()]
        if command == "FETCH":
            uid = int(args[0])
            raw = self.messages.get(self.selected_folder, {}).get(uid)
            if raw is None:
                return "NO", [None]
            return "OK", [(f"{uid} (RFC822 {{{len(raw)}}}".encode(), raw)]
        raise NotImplementedError(command)

    def _search(self, criteria):
        uids = sorted(self.messages.get(self.selected_folder, {}).keys())
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
