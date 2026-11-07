"""Manually reviewed item-name differences for exact source snapshots.

These are explicit bindings, never automatic spelling or synonym matches.
Changing either source invalidates the exception and restores ordinary checks.
"""

REVIEWED_ITEM_BINDINGS = (
    {
        'scene_id': 'ch502ADifferenceofSpendingLeisureTimeMuttrahSouq',
        'bundle_sha256': 'c8fc650ed5f5e33d3f7b168aafc77ca71c2c5a13ec51a5a9b8238ea9c9f9ebbe',
        'item_manager_sha256': '63fe5448ef7157c4c96d038a50de88f3883a2c84f67e9a7a9c9d2841cf82546f',
        'manager_key': 'hanginglamp_502_mut_sou_ini',
        'encoded_name': 'hanginglampcopy',
        'object_id': 98,
        'evidence': 'docs/validation/reviewed_item_aliases.json',
    },
    {
        'scene_id': 'ch509EasterRingHuntEasterFestiveMall',
        'bundle_sha256': '1ff326c9906b657ba4e5aaa6a29ae5536b62a4eb79e799ee8eda2260018e926d',
        'item_manager_sha256': '435145a7522e2426e686c9b619ea06f26bc069b831b4f5c5bc41a9a00fa6f5a4',
        'manager_key': 'colourfulball_509_eas_mal_ini',
        'encoded_name': 'colourfullball',
        'object_id': 96,
        'evidence': 'docs/validation/reviewed_item_aliases.json',
    },
    {
        'scene_id': 'ch539ATourGuideTrapNYCVeteransParade',
        'bundle_sha256': '5c8433bdfb2e28e82ee55068c1acfba6980934dba08e2ad82677a9f6f77f5e85',
        'item_manager_sha256': '2073bf52a161ab162e73a4d80e0f4ca9f52b15fc5aa89d55aea0593a18b624de',
        'manager_key': 'questands_539_nyc_par_ini',
        'encoded_name': 'questand',
        'object_id': 122,
        'evidence': 'docs/validation/reviewed_item_aliases.json',
    },
    {
        'scene_id': 'ch560MissingOldFriendsADaysWork',
        'bundle_sha256': '2d97505058fccba976c894f92c3cb9af1cfb0e958e71dcfc6a5b48f5a69b5a61',
        'item_manager_sha256': 'a93b55bdaec74738d98ab45b905e14943f019d1d68d60a6eb3a4b849b464a79b',
        'manager_key': 'mask_560_day_wor_ini',
        'encoded_name': 'facemask',
        'object_id': 104,
        'evidence': 'docs/validation/reviewed_item_aliases.json',
    },
    {
        'scene_id': 'ch614SearchforInformationDeductionintheDrawingRoom',
        'bundle_sha256': '271271369da81a18256c6028fa2344b0ced7c4adee61488922cae52eb5bfe06e',
        'item_manager_sha256': '1a8be03d25525642a099ed86d58c9a1d857a74a89aa85a5c70690be44d4fda55',
        'manager_key': 'candelabra_614_ded_roo_ini',
        'encoded_name': 'candlelabra',
        'object_id': 74,
        'evidence': 'docs/validation/reviewed_item_aliases.json',
    },
    {
        'scene_id': 'ch678ConfessionsinGlassHometownGlory',
        'bundle_sha256': '40ae76d2eea57199cb9c3250bdc2fde2396ad27400456dcce085fd5d2545a3b7',
        'item_manager_sha256': '462d4b1b05f433787762d0e497c6d25fe147195c919808a6cdd914f60e3fe0bd',
        'manager_key': 'burgar_678_hom_glo_ini',
        'encoded_name': 'bargar',
        'object_id': 96,
        'evidence': 'docs/validation/reviewed_item_aliases.json',
    },
)


def reviewed_item_bindings(bundle_sha256, item_manager_sha256):
    """Return fresh copies only for the reviewed bundle and canonical manager."""
    matches = [entry for entry in REVIEWED_ITEM_BINDINGS
               if entry['bundle_sha256'] == bundle_sha256
               and entry['item_manager_sha256'] == item_manager_sha256]
    keys = [entry['manager_key'] for entry in matches]
    if len(keys) != len(set(keys)):
        raise ValueError('Conflicting reviewed item bindings for source snapshot')
    return {entry['manager_key']: dict(entry) for entry in matches}
