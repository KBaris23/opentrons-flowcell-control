"""Generated Opentrons protocol."""

from opentrons import protocol_api

metadata = {
    "protocolName": 'Titration_5x5uL',
    "author": 'Opentrons Flowcell Console',
    "description": '5-step titration, pause after each addition',
    "source": "opentrons-flowcell-control-ui",
    "apiLevel": '2.19',
}

requirements = {
    "robotType": 'OT-2',
}


def run(protocol: protocol_api.ProtocolContext):
    tips = protocol.load_labware('opentrons_96_filtertiprack_20ul', '4')
    stock = protocol.load_labware('opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap', '5')
    dilute = protocol.load_labware('opentrons_10_tuberack_falcon_4x50ml_6x15ml_conical', '6')
    pipette = protocol.load_instrument('p20_single_gen2', 'left', tip_racks=[tips])

    protocol.comment('Titration step 1')
    pipette.transfer(5, stock['A1'], dilute['A1'], new_tip='always')
    protocol.pause('Run syringe pull and SWV for titration step, then resume.')
    protocol.comment('Titration step 2')
    pipette.transfer(5, stock['A1'], dilute['A1'], new_tip='always')
    protocol.pause('Run syringe pull and SWV for titration step, then resume.')
    protocol.comment('Titration step 3')
    pipette.transfer(5, stock['A1'], dilute['A1'], new_tip='always')
    protocol.pause('Run syringe pull and SWV for titration step, then resume.')
    protocol.comment('Titration step 4')
    pipette.transfer(5, stock['A1'], dilute['A1'], new_tip='always')
    protocol.pause('Run syringe pull and SWV for titration step, then resume.')
    protocol.comment('Titration step 5')
    pipette.transfer(5, stock['A1'], dilute['A1'], new_tip='always')
    protocol.comment('Titrations complete')
