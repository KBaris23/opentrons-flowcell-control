"""Generated Opentrons protocol."""

from opentrons import protocol_api

metadata = {
    "protocolName": '5x5ul titration no pause single script new',
    "author": 'Opentrons Flowcell Console',
    "description": 'Generated from the UI builder.',
    "source": "opentrons-flowcell-control-ui",
    "apiLevel": '2.19',
}

requirements = {
    "robotType": 'OT-2',
}


def run(protocol: protocol_api.ProtocolContext):
    stock = protocol.load_labware('opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap', '2')
    dilute = protocol.load_labware('opentrons_6_tuberack_falcon_50ml_conical', '3')
    tips = protocol.load_labware('opentrons_96_tiprack_20ul', '7')
    pipette = protocol.load_instrument('p20_single_gen2', 'left', tip_racks=[tips])
    pipette.starting_tip = tips['G3']

    protocol.comment('titration step 1')
    pipette.transfer(5, stock['D1'].center(), dilute['A2'].center(), new_tip='always')
    protocol.comment('titration step 2')
    pipette.transfer(5, stock['D1'].center(), dilute['A2'].center(), new_tip='always')
    protocol.comment('titration step 3')
    pipette.transfer(5, stock['D1'].center(), dilute['A2'].center(), new_tip='always')
    protocol.comment('titration step 4')
    pipette.transfer(5, stock['D1'].center(), dilute['A2'].center(), new_tip='always')
    protocol.comment('titration step 5')
    pipette.transfer(5, stock['D1'].center(), dilute['A2'].center(), new_tip='always')
    protocol.comment('titration done')
