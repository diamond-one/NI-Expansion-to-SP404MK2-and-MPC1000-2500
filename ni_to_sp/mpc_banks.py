"""Four non-recursive 16-pad banks inside an editable MPC program."""
import copy
from .kit_editor import empty_kit, refresh

FIELDS = {'banks', 'active_bank', 'program_id', 'program_name', 'bank_kit'}


def snapshot(group):
    bank = copy.deepcopy({key: value for key, value in group.items() if key not in FIELDS})
    bank['kit'] = copy.deepcopy(group.get('bank_kit', group['kit']))
    return refresh(bank)


def sync_banks(group):
    if 'banks' in group:
        group['banks'][group.get('active_bank', 0)] = snapshot(group)
    return group


def ensure_banks(group):
    group = copy.deepcopy(group)
    if 'banks' not in group:
        group['banks'] = [snapshot(group), *[empty_kit('Empty bank ' + letter) for letter in 'BCD']]
        group['active_bank'] = 0
        group['program_id'] = group['kit']['id']
        group['program_name'] = group['kit']['name']
        group['bank_kit'] = copy.deepcopy(group['kit'])
    return sync_banks(group)


def activate(group, index):
    if isinstance(index, bool) or index not in range(4):
        raise ValueError('Choose MPC bank A, B, C or D.')
    program = ensure_banks(group)
    bank = copy.deepcopy(program['banks'][index])
    bank.update(banks=program['banks'], active_bank=index,
                program_id=program['program_id'], program_name=program['program_name'],
                bank_kit=copy.deepcopy(bank['kit']))
    # The document identity/name remain stable while source metadata follows the bank.
    if bank['kit'].get('path'):
        bank['origin_kit'] = copy.deepcopy(bank['kit'])
    bank['kit'].update(id=program['program_id'], name=program['program_name'])
    return refresh(bank)


def all_banks(group):
    if 'banks' not in group:
        return [snapshot(group)]
    program = ensure_banks(group)
    return program['banks']


def counts(group):
    banks = all_banks(group)
    return sum(bank['available'] for bank in banks), sum(len(bank['unresolved']) for bank in banks)
