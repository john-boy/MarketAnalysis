"""Top-level tab widgets.

Each tab is a ``QWidget`` subclass owning its own layout.  Tabs obtain
data exclusively through the service layer — they must not import
``market_analysis.data`` or ``market_analysis.sources`` directly.
"""
