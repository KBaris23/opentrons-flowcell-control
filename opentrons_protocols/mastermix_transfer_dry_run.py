"""Minimal bundled OT-2 transfer example.

This protocol is intentionally simple and mirrors the sample the user added,
but uses clean naming and ASCII-only comments so it can live comfortably inside
the main application repository.
"""

from opentrons import protocol_api


metadata = {
    "protocolName": "Mastermix Transfer Dry Run",
    "author": "OpenTrons Flowcell Console",
    "description": "Transfer 1 uL from tube A1 to tube A2 with a P20 single.",
    "source": "opentrons-flowcell-control",
    "apiLevel": "2.19",
}

requirements = {
    "robotType": "OT-2",
}


def run(protocol: protocol_api.ProtocolContext):
    tube_rack = protocol.load_labware(
        "opentrons_24_tuberack_nest_2ml_snapcap",
        6,
    )
    tiprack_20 = protocol.load_labware("opentrons_96_filtertiprack_20ul", 4)
    p20 = protocol.load_instrument("p20_single_gen2", "left", tip_racks=[tiprack_20])

    source_well = tube_rack["A1"]
    dest_well = tube_rack["A2"]
    p20.transfer(1, source_well, dest_well, new_tip="once")
