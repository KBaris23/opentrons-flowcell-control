"""Generated Opentrons protocol."""

from opentrons import protocol_api

metadata = {
    "protocolName": 'Titration_Kana_10_steps',
    "author": 'Opentrons Flowcell Console',
    "description": 'titration with 0, 0.975, 0.950, 1.8551, 33,604, 7.017, 13.667, 26.660, 52.219, 103.211 uL pipetting',
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
    pipette_secondary = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tips_200ul])
    pipette_secondary.starting_tip = tips_200ul['A2']

    protocol.comment('start titration step 1')
    pipette_primary.transfer(0.975, stock['D1'].bottom(2), dilute['A2'].bottom(2), new_tip='always')
    protocol.pause('first step done')
    protocol.comment('start titration step 2')
    pipette_primary.transfer(0.95, stock['D1'].bottom(2), dilute['A2'].bottom(2), new_tip='always')
    protocol.pause('step 2 done')
    protocol.comment('start titration step 3')
    pipette_primary.transfer(1.851, stock['D1'].bottom(2), dilute['A2'].bottom(2), new_tip='always')
    protocol.pause('step 3 done')
    protocol.comment('start titration step 4')
    pipette_primary.transfer(3.604, stock['D1'].bottom(2), dilute['A2'].bottom(2), new_tip='always')
    protocol.pause('step 4 done')
    protocol.comment('start titration step 5')
    pipette_primary.transfer(7.017, stock['D1'].bottom(2), dilute['A2'].bottom(2), new_tip='always')
    protocol.pause('step 5 done')
    protocol.comment('start titration step 6')
    pipette_primary.transfer(13.667, stock['D1'].bottom(2), dilute['A2'].bottom(2), new_tip='always')
    protocol.pause('step 6 done')
    protocol.comment('start titration step 7')
    pipette_secondary.transfer(26.66, stock['D1'].bottom(2), dilute['A2'].bottom(2), new_tip='always')
    protocol.pause('step 7 done')
    protocol.comment('start titration step 8')
    pipette_secondary.transfer(52.219, stock['D1'].bottom(2), dilute['A2'].bottom(2), new_tip='always')
    protocol.pause('step 8 done')
    protocol.comment('start titration step 9')
    pipette_secondary.transfer(103.211, stock['D1'].bottom(2), dilute['A2'].bottom(2), new_tip='always')
    protocol.comment('titration completed')
