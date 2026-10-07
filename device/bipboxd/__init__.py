"""The bipbox device daemon.

Owns every shared resource on the box — the SPI bus and the shift register, the
GPIO lines, the sound card, the printer, and the single connection to the
server — and runs the telex, telegraphy and VoIP features as tasks inside one
process (architecture.md §2.1).
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
