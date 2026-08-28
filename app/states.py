from aiogram.fsm.state import State, StatesGroup


class BotStates(StatesGroup):
    waiting_emm = State()
    waiting_to = State()
    waiting_info = State()
    regions_projects = State()
    regions_manager = State()
    regions_regions = State()
