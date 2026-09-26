"""Representative wire commands for every OS 0.2.1 screen, shared by the firmware host tests and by
tools/preview_screens.py. Each value is (wire command, designer capture or None). The sub-field
separator is the single byte 0xB7, so strings are meant to be encoded as latin-1."""

O = '\xb7'


def r(*f):
    return O.join(f)


SCREENS = {
    'home': ('HOME2|12:44 PM|0|3', '12_home_contacts_third.png'),
    'home_read': ('HOME2|12:44 PM|2|3', None),
    'home_listen': ('HOME2|12:44 PM|3|3', None),
    'home_contacts': ('HOME2|12:44 PM|4|3', None),
    'home_both': ('HOME2|12:44 PM|1|3|B', None),
    'home_words': ('HOME2|12:44 PM|0|3|W', None),
    'home_icons_end': ('HOME2|12:44 PM|4|0|I', None),
    'texts': ('TEXTS|2|' + '|'.join([
        r('Pip Okonkwo', 'omw, 5 min', '1', '6:57 PM'), r('(555) 019-9002', 'Where are you?', '0', '9:29 AM'),
        r('(555) 019-9014', '! thank you!', '0', '8:31 AM'), r('Therapist', 'See you Thursday at 3.', '0', '2:14 PM'),
        r('Bestie', 'STOP EVERYTHING. call me', '0', 'Yesterday')]), '01_texts_list_5_rows.png'),
    'texts_empty': ('TEXTS|-2', None),                # an empty list opens with + selected
    'music': ('MUSIC|0|' + '|'.join([
        r('NOW PLAYING', 'The Long Road Home - Nina Simone', 'PLAYING'), r('Pastel Blues', 'Nina Simone', '9 trk'),
        r('Kind of Blue', 'Miles Davis', '5 trk')]), None),
    'music_empty': ('MUSIC|-1', None),
    'tracks': ('TRACKS|1|Pastel Blues|' + '|'.join([
        r('Plain Gold Ring', 'Nina Simone', '3:05'), r('Sinnerman', 'Nina Simone', '10:21'), r('Be My Husband', 'Nina Simone', '2:52')]), None),
    'nowplaying': ('NOWPLAYING|P|Sinnerman|Nina Simone|Pastel Blues|93|621|40|2/9', None),
    'nowplaying_paused': ('NOWPLAYING|U|Sinnerman (Live at the Village Gate, extended studio version)|Nina Simone|Pastel Blues (Remastered)|300|621|70|2/9', None),
    'nowplaying_finished': ('NOWPLAYING|S|Sinnerman|Nina Simone|Pastel Blues|0|621|100|9/9', None),
    'home_playing': ('HOME2|12:44 PM|3|0|I|1', None),
    'home_playing_unselected': ('HOME2|12:44 PM|0|0|I|1', None),
    'library': ('LIBRARY|1|' + '|'.join([
        r("Alice's Adventures in Wond", 'Lewis Carroll', '35%'), r('The Count of Monte Cristo', 'Alexandre Dumas', ''),
        r('Frankenstein', 'Mary Shelley', '100%')]), None),
    'library_empty': ('LIBRARY|-1', None),
    'contacts': ('CONTACTSPICK|0||6 / 12|' + '|'.join([
        r('Gina Rossi', '(312) 555-0188'), r('Jordan Reyes', '(646) 555-0118'), r('Mom', '(203) 555-0187'),
        r('Pip Okonkwo', '(917) 555-0101'), r('Rafael Ortiz', '(305) 555-0121'), r('Sam Whitfield', ''),
        r('Therapist', '(212) 555-0199')]), '11_contacts_windowed_end_of_list.png'),
    'contacts_nomatch': ('CONTACTSPICK|0|zz|', None),
    'calls': ('CALLS|1|' + '|'.join([
        r('DIAL A NUMBER', 'NEW', '', ''), r('Pip', 'OUT', '4:03 PM', '12:04'),
        r('(555) 019-9002', 'MISS', '11:47 AM', ''), r('Mom', 'IN', 'Yesterday', '3:12')]), None),
    'thread_sending': ('THREAD2|Pip Okonkwo|||' + '|'.join([
        r('R', '6:52 PM', 'you close?'), r('Y1', '6:53 PM', 'yeah leaving now'), r('Y0', '6:55 PM', 'still here?')]),
        '04_thread_sending_and_sent.png'),
    'thread_retry': ('THREAD2|Pip Okonkwo|||' + '|'.join([
        r('R', '6:52 PM', 'you close?'), r('Y1', '6:53 PM', 'yeah leaving now'), r('Y3', '6:55 PM', 'still here?')]),
        '03_thread_not_sent_selected_retry.png'),
    'thread_notsent': ('THREAD2|Pip Okonkwo|||' + '|'.join([
        r('R', '6:52 PM', 'you close?'), r('Y1', '6:53 PM', 'yeah leaving now'), r('Y2', '6:55 PM', 'still here?')]), None),
    'thread_long': ('THREAD2|Pip Okonkwo|...seat near the window if you can, the back row is impossible to|B|' + '|'.join([
        r('R', '6:52 PM', 'you close?'),
        r('Y2', '6:55 PM', 'This is a longer message that wraps onto several lines of the bubble')]), None),
    'compose': ('COMPOSE|Alice Test|hey are you free tonight? I was thinking we could grab dinner|0||0|1', None),
    'compose_empty': ('COMPOSE|||1||0|0', None),
    'alert_bad_number': ('STUB|CONTACT|THAT NUMBER CANNOT BE DIALED. A NUMBER NEEDS TEN DIGITS, OR ELEVEN STARTING WITH 1. '
                         'SPACES, DASHES AND BRACKETS ARE FINE.', '10_validation_bad_number.png'),
    'alert_read': ('STUB|READ|READ CANNOT OPEN YET. THIS BUILD CARRIES TEXT AND CALL ONLY, AND NO BOOKS ARE ON THE PHONE. '
                   'PRESS ENTER TO GO BACK TO THE MENU.', None),
    'confirm_delete': ('CONFIRM|DELETE CONTACT|DELETE PIP OKONKWO? THE MESSAGES STAY IN THE TEXT LIST, LABELED WITH THE '
                       'NUMBER. THE NAME CANNOT BE BROUGHT BACK.|DELETE|KEEP CONTACT|K', '09_delete_contact_confirm.png'),
    'confirm_delete_go': ('CONFIRM|DELETE CONTACT|DELETE PIP OKONKWO? THE MESSAGES STAY IN THE TEXT LIST, LABELED WITH THE '
                          'NUMBER. THE NAME CANNOT BE BROUGHT BACK.|DELETE|KEEP CONTACT|D', None),
    'confirm_discard': ('CONFIRM|NEW MESSAGE|DISCARD THIS MESSAGE? IT HAS NOT BEEN SENT, AND THE PHONE KEEPS NO DRAFTS, '
                        'SO THE TEXT CANNOT BE BROUGHT BACK.|DISCARD|KEEP EDITING|K', None),
    'contact_saved': ('CONTACT|Pip Okonkwo|(917) 555-0101|S|C', None),
    'contact_unsaved': ('CONTACT|(555) 019-9002|NOT IN CONTACTS|U|V', '06_contact_unsaved_number_save.png'),
    'contact_nonum': ('CONTACT|Sam Whitfield|NO NUMBER SAVED|N|A', None),
    'edit_new': ('CONTACTEDIT|||(555) 019-9002|0|N', '07_new_contact_prefilled.png'),
    'edit_edit': ('CONTACTEDIT|Pip|Okonkwo|(917) 555-0101|3|E', None),
    'edit_delete': ('CONTACTEDIT|Pip|Okonkwo|(917) 555-0101|4|E', '08_edit_contact_delete_selected.png'),
    'thread_call': ('THREAD2|Pip Okonkwo|||' + r('C', '', 'LAST CALL: MISSED, YESTERDAY') + '|' +
                    r('R', '9:02 AM', 'Are we still on for lunch?') + '|' + r('Y1', '9:05 AM', 'Yes, see you at noon'), None),
    'upload_empty': ('UPLOAD|192.168.1.23:8080|4821|', None),
    'upload_got': ('UPLOAD|192.168.1.23:8080|4821|Moby Dick.epub' + O + '01 So What.mp3' + O + 'Contacts: 3 added', None),
    'chapters': ('CHAPTERS|1|' + '|'.join([r('The Beginning', '0%', ''), r('A Very Long Middle...', '12%', 'HERE'),
                                           r('The End', '88%', '')]), None),
    'home_notes': ('HOME2|12:44 PM|5|0|I', None),
    'home_settings': ('HOME2|12:44 PM|6|0|I', None),
    'notes': ('NOTES|1|' + '|'.join([r('Groceries', '9:41 AM', ''), r('Ideas for the case', 'Yesterday', ''),
                                     r('Books to read', 'MONDAY', '')]), None),
    'notes_empty': ('NOTES|-2', None),
    'note': ('NOTE||Groceries' + O + 'eggs' + O + 'milk' + O + '' + O + 'and bread', None),
    'note_back': ('NOTE|B|Groceries' + O + 'eggs', None),
    'note_delete': ('NOTE|D|Groceries' + O + 'eggs', None),
    'note_long': ('NOTE||...' + O + O.join(['x' * 30] * 7) + O, None),
    'settings': ('SETTINGS|0|' + r('Wi-Fi', 'Connected: Maple', '') + '|' + r('Bluetooth', 'Not connected', ''), None),
    'netlist_wifi': ('NETLIST|W|2|' + '|'.join([r('Wi-Fi', 'Wi-Fi is on', 'ON'), r('Maple', 'Connected', ''),
                                                r('Birch_5G', 'Secured', ''), r('OpenCafe', 'Open', ''),
                                                r('Willow_Street_5G', 'Secured', '')]), None),
    'netlist_bt': ('NETLIST|B|1|' + '|'.join([r('Bluetooth', 'Bluetooth is on', 'ON'), r('Keyboard', 'Connected', ''),
                                              r('Headphones', 'Not connected', ''),
                                              r('PAIR NEW DEVICE', 'Put the device in pairing mode first', '')]), None),
    'netlist_empty': ('NETLIST|W|0|' + r('Wi-Fi', 'Wi-Fi is on', 'ON') + '|' + r('SEARCH AGAIN', 'No networks found', ''), None),
    'netlist_scanning': ('NETLIST|W|0|' + '|'.join([r('Wi-Fi', 'Wi-Fi is on', 'ON'), r('Maple', 'Connected', ''),
                                                    r('SEARCHING...', '', '')]), None),
    'netlist_off': ('NETLIST|B|0|' + r('Bluetooth', 'Bluetooth is off', 'OFF'), None),
    'netlist_pair': ('NETLIST|P|0|' + r('Speaker', 'New device', '') + '|' + r('SEARCH AGAIN', '', ''), None),
    'lightset_off': ('LIGHTSET|0', None),
    'lightset_mid': ('LIGHTSET|5', None),
    'netpass': ('NETPASS|Willow_Street_5G|*******|', None),
    'netpass_back': ('NETPASS|Willow_Street_5G|*******|B', None),
    'netstate_working': ('NETSTATE|W|WORKING|Connecting to Willow_Street_5G...', None),
    'netstate_ok': ('NETSTATE|B|OK|Connected to Headphones.', None),
    'netstate_fail': ('NETSTATE|W|FAIL|wrong password', None),
}


def encode(wire):
    return wire.encode('latin-1')
