"""Generated Opentrons protocol."""

from opentrons import protocol_api

metadata = {
    "protocolName": 'autoimmobilization',
    "author": 'Opentrons Flowcell Console',
    "description": 'wait 1hr, pull aptamer solution replace with c6, wait 1hr, pull c6 replace with buffer, alert',
    "source": "opentrons-flowcell-control-ui",
    "apiLevel": '2.19',
}

requirements = {
    "robotType": 'OT-2',
}


def run(protocol: protocol_api.ProtocolContext):
    tips_20ul = protocol.load_labware('opentrons_96_filtertiprack_20ul', '7')
    tips_200ul = protocol.load_labware('opentrons_96_filtertiprack_200ul', '8')
    reagents = protocol.load_labware('opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap', '2')
    pipette_primary = protocol.load_instrument('p20_single_gen2', 'left', tip_racks=[tips_20ul])
    pipette_secondary = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tips_200ul])
    pipette_secondary.starting_tip = tips_200ul['A2']

    pipette_secondary.pick_up_tip()
    pipette_secondary.transfer(200, reagents['D2'].bottom(2), reagents['B3'].bottom(2), new_tip='never')
    pipette_secondary.transfer(200, reagents['D2'].bottom(2), reagents['B3'].bottom(2), new_tip='never')
    pipette_secondary.transfer(100, reagents['D2'].bottom(2), reagents['B3'].bottom(2), new_tip='never')
    pipette_secondary.drop_tip()
    protocol.comment('aptamer solution removed')
    pipette_secondary.pick_up_tip()
    pipette_secondary.transfer(200, reagents['B1'].bottom(2), reagents['D2'].bottom(2), new_tip='never')
    pipette_secondary.transfer(200, reagents['B1'].bottom(2), reagents['D2'].bottom(2), new_tip='never')
    pipette_secondary.transfer(100, reagents['B1'].bottom(2), reagents['D2'].bottom(2), new_tip='never')
    pipette_secondary.drop_tip()
    protocol.comment('c6 added')
    protocol.pause('pause for a 1hr wait')
    pipette_secondary.pick_up_tip()
    pipette_secondary.transfer(200, reagents['D2'].bottom(2), reagents['B3'].bottom(2), new_tip='never')
    pipette_secondary.transfer(200, reagents['D2'].bottom(2), reagents['B3'].bottom(2), new_tip='never')
    pipette_secondary.transfer(100, reagents['D2'].bottom(2), reagents['B3'].bottom(2), new_tip='never')
    pipette_secondary.drop_tip()
    protocol.comment('aptamer solution removed')
    pipette_secondary.pick_up_tip()
    pipette_secondary.transfer(200, reagents['B2'].bottom(2), reagents['D2'].bottom(2), new_tip='never')
    pipette_secondary.transfer(200, reagents['B2'].bottom(2), reagents['D2'].bottom(2), new_tip='never')
    pipette_secondary.transfer(100, reagents['B2'].bottom(2), reagents['D2'].bottom(2), new_tip='never')
    pipette_secondary.drop_tip()
    protocol.comment('buffer solution added')
