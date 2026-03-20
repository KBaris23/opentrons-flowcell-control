"""Generated Opentrons protocol."""

from opentrons import protocol_api

metadata = {
    "protocolName": 'Generated Protocol',
    "author": 'Opentrons Flowcell Console',
    "description": 'Generated from the UI builder.',
    "source": "opentrons-flowcell-control-ui",
    "apiLevel": '2.19',
}

requirements = {
    "robotType": 'OT-2',
}


def run(protocol: protocol_api.ProtocolContext):
    tips = protocol.load_labware('opentrons_96_filtertiprack_20ul', '4')
    source = protocol.load_labware('opentrons_24_tuberack_nest_2ml_snapcap', '6')
    dest = protocol.load_labware('opentrons_24_tuberack_nest_2ml_snapcap', '7')
    pipette = protocol.load_instrument('p20_single_gen2', 'left', tip_racks=[tips])

    pipette.transfer(1, source['A1'], dest['A2'], new_tip='once')
    pipette.transfer(1, source['A1'], dest['A2'], new_tip='once')
    pipette.transfer(1, source['A1'], dest['A2'], new_tip='once')
