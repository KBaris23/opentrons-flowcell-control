"""Generated Opentrons protocol."""

from opentrons import protocol_api

metadata = {
    "protocolName": 'dual_switching_dry_run',
    "author": 'Opentrons Flowcell Console',
    "description": 'dual pipetting',
    "source": "opentrons-flowcell-control-ui",
    "apiLevel": '2.19',
}

requirements = {
    "robotType": 'OT-2',
}


def run(protocol: protocol_api.ProtocolContext):
    tips_20ul = protocol.load_labware('opentrons_96_filtertiprack_20ul', '7')
    tips_200ul = protocol.load_labware('opentrons_96_filtertiprack_200ul', '8')
    stock = protocol.load_labware('opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap', '2')
    dilute = protocol.load_labware('opentrons_6_tuberack_falcon_50ml_conical', '3')
    pipette_primary = protocol.load_instrument('p20_single_gen2', 'left', tip_racks=[tips_20ul])
    pipette_primary.starting_tip = tips_20ul['F4']
    pipette_secondary = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tips_200ul])
    pipette_secondary.starting_tip = tips_200ul['E1']

    pipette_primary.transfer(5, stock['D1'].bottom(2), dilute['A2'].bottom(2), new_tip='always')
    pipette_secondary.transfer(25, stock['D1'].bottom(2), dilute['A2'].bottom(2), new_tip='always')
