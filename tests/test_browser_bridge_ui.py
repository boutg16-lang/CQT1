from tools.browser_bridge import client


class FakeWidget:
    def __init__(self, **options):
        self.options = options
        self.pack_options = None

    def pack(self, **options):
        self.pack_options = options


class FakeTtk:
    def __init__(self):
        self.checkbutton = None
        self.label = None

    def Checkbutton(self, parent, **options):
        self.checkbutton = FakeWidget(**options)
        return self.checkbutton

    def Label(self, parent, **options):
        self.label = FakeWidget(**options)
        return self.label


def test_wrapping_is_applied_to_supported_label_not_checkbutton():
    ttk = FakeTtk()
    consent = object()

    client._add_consent_controls(ttk, object(), consent)

    assert ttk.checkbutton.options["variable"] is consent
    assert "wraplength" not in ttk.checkbutton.options
    assert ttk.label.options["wraplength"] == 545
    assert "screenshots" in ttk.label.options["text"]
    assert "Stop" in ttk.label.options["text"]
    assert "15-minute" not in ttk.checkbutton.options["text"]
