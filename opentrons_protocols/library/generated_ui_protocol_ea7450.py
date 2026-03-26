"""Generated Opentrons protocol."""

from opentrons import protocol_api

metadata = {
    "protocolName": 'max_training',
    "author": 'Opentrons Flowcell Console',
    "description": 'titration training',
    "source": "opentrons-flowcell-control-ui",
    "apiLevel": '2.19',
}

requirements = {
    "robotType": 'OT-2',
}


def run(protocol: protocol_api.ProtocolContext):
    tips = protocol.load_labware('opentrons_96_filtertiprack_20ul', '7')
    stock = protocol.load_labware('opentrons_24_tuberack_nest_2ml_snapcap', '6')
    dilute = protocol.load_labware('opentrons_10_tuberack_falcon_4x50ml_6x15ml_conical', '3')
    pipette = protocol.load_instrument('p20_single_gen2', 'left', tip_racks=[tips])

    protocol.comment('step 1')
    pipette.transfer(5, stock['A1'], dilute['A1'], new_tip='always')
    protocol.pause('Run syringe pull and SWV for titration step 1, then resume.')
    pipette.transfer(5, stock['A1'], dilute['A1'], new_tip='always')
    protocol.pause('Run syringe pull and SWV for titration step 1, then resume.')
    pipette.transfer(5, stock['A1'], dilute['A1'], new_tip='always')
    protocol.pause('Run syringe pull and SWV for titration step 1, then resume.')
    pipette.transfer(5, stock['A1'], dilute['A1'], new_tip='always')
    protocol.pause('Run syringe pull and SWV for titration step 1, then resume.')
    pipette.transfer(5, stock['A1'], dilute['A1'], new_tip='always')
