"""Generated Opentrons protocol."""

from opentrons import protocol_api

metadata = {
    "protocolName": 'Titration_Amp0_10_steps',
    "author": 'Opentrons Flowcell Console',
    "description": 'titration with 0, 5.1, 5.2, 10.5, 21.2, 43,, 87.5, 180, 375, 820 uL pipetting',
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
    pipette_primary.transfer(5.1, stock['D1'].bottom(2), dilute['B2'].bottom(2), new_tip='always')
    protocol.pause('first step done')
    protocol.comment('start titration step 2')
    pipette_primary.transfer(5.2, stock['D1'].bottom(2), dilute['B2'].bottom(2), new_tip='always')
    protocol.pause('step 2 done')
    protocol.comment('start titration step 3')
    pipette_primary.transfer(10.5, stock['D1'].bottom(2), dilute['B2'].bottom(2), new_tip='always')
    protocol.pause('step 3 done')
    protocol.comment('start titration step 4')
    pipette_secondary.transfer(21.2, stock['D1'].bottom(2), dilute['B2'].bottom(2), new_tip='always')
    protocol.pause('step 4 done')
    protocol.comment('start titration step 5')
    pipette_secondary.transfer(43, stock['D1'].bottom(2), dilute['B2'].bottom(2), new_tip='always')
    protocol.pause('step 5 done')
    protocol.comment('start titration step 6')
    pipette_secondary.transfer(87.5, stock['D1'].bottom(2), dilute['B2'].bottom(2), new_tip='always')
    protocol.pause('step 6 done')
    protocol.comment('start titration step 7')
    pipette_secondary.transfer(180, stock['D1'].bottom(2), dilute['B2'].bottom(2), new_tip='always')
    protocol.pause('step 7 done')
    protocol.comment('start titration step 8')
    pipette_secondary.transfer(200, stock['D1'].bottom(2), dilute['B2'].bottom(2), new_tip='always')
    pipette_secondary.transfer(175, stock['D1'].bottom(2), dilute['B2'].bottom(2), new_tip='always')
    protocol.pause('step 8 done')
    protocol.comment('start titration step 9')
    pipette_secondary.transfer(200, stock['D1'].bottom(2), dilute['B2'].bottom(2), new_tip='always')
    pipette_secondary.transfer(200, stock['D1'].bottom(2), dilute['B2'].bottom(2), new_tip='always')
    pipette_secondary.transfer(200, stock['D1'].bottom(2), dilute['B2'].bottom(2), new_tip='always')
    pipette_secondary.transfer(200, stock['D1'].bottom(2), dilute['B2'].bottom(2), new_tip='always')
    pipette_primary.transfer(20, stock['D1'].bottom(2), dilute['B2'].bottom(2), new_tip='always')
    protocol.comment('titration completed')
