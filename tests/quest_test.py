"""Offline: Forever quest details -> native button -> decoded companion record -> chat.

Run with python tests/quest_test.py (lupa, no game, gateway or model calls).
"""
import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sim import ROOT, Sim, hermeswow


QUEST_API = """
function GetLocale() return 'esES' end
function GetRealZoneText() return 'Bosque de Elwynn' end
function GetSubZoneText() return 'Villadorada' end
function UnitLevel(unit) if unit == 'player' then return 23 end end
function UnitClass(unit) if unit == 'player' then return 'Cazador', 'HUNTER' end end
function UnitRace(unit) if unit == 'player' then return 'Enano', 'Dwarf' end end
function UnitFactionGroup(unit) if unit == 'player' then return 'Alliance', 'Alianza' end end
function UnitName() return 'PrivateCharacter' end
function GetRealmName() return 'PrivateRealm' end

C_Map = {
    GetBestMapForUnit = function(unit) assert(unit == 'player'); return 37 end,
    GetPlayerMapPosition = function(map, unit)
        if map == 37 and unit == 'player' then
            return { GetXY = function() return 0.42, 0.657 end }
        end
    end,
}

local titles = { [42] = 'Colmillos del bosque', [43] = 'Entrega en Villadorada' }
objectiveReads = 0
C_QuestLog = {
    GetTitleForQuestID = function(id) return titles[id] end,
    GetQuestObjectives = function(id)
        objectiveReads = objectiveReads + 1
        if id == 42 then
            return {
                { text = '|cffabcdefColmillos|r', numFulfilled = 3, numRequired = 8, finished = false },
                { text = 'Encuentra a la vigía', numFulfilled = 0, numRequired = 0, finished = true },
            }
        end
    end,
    IsComplete = function(id) return id == 43 end,
}

MenuUtil = { CreateContextMenu = function(_, build)
    STUB.questMenu = {}
    local function menu(entries)
        return {
            SetScrollMode = function() end,
            CreateButton = function(_, label, callback)
                local children = {}
                local item = menu(children)
                item.children = children
                item.click = callback
                entries[label] = item
                return item
            end,
        }
    end
    build(nil, menu(STUB.questMenu))
end }

local create = CreateFrame
function CreateFrame(kind, name, parent, template)
    local f = create(kind, name, parent, template)
    local setPoint = f.SetPoint
    f.SetPoint = function(self, point, relative, relativePoint, x, y)
        self._anchors = self._anchors or {}
        self._anchors[point] = { point, relative, relativePoint, x, y }
        setPoint(self, point, relative, relativePoint, x, y)
    end
    f.GetPoint = function(self, point)
        local anchors = self._anchors or {}
        return unpack(anchors[point] or anchors.BOTTOMLEFT or {})
    end
    return f
end

local function AddFooter(panel, height, scrollHeight)
    panel.AbandonButton = CreateFrame('Button', nil, panel)
    panel.ShareButton = CreateFrame('Button', nil, panel)
    panel.TrackButton = CreateFrame('Button', nil, panel)
    panel.AbandonButton:SetHeight(height)
    panel.ScrollFrame = CreateFrame('ScrollFrame', nil, panel)
    panel.ScrollFrame:SetHeight(scrollHeight)
end

function STUB.AddQuestPanels()
    local map = CreateFrame('Frame', 'QuestMapFrame', UIParent)
    local details = CreateFrame('Frame', nil, map)
    map.DetailsFrame = details
    details.BackFrame = CreateFrame('Frame', nil, details)
    details.BackFrame.BackButton = CreateFrame('Button', nil, details.BackFrame)
    AddFooter(details, 22, 430)
    details.RewardsFrameContainer = CreateFrame('Frame', nil, details)
    details.RewardsFrameContainer:SetPoint('BOTTOMLEFT', details, 'BOTTOMLEFT', 0, 23)
    details:Hide()
    function QuestMapFrame_ShowQuestDetails(id)
        details.questID = id
        details:Show()
    end
    function QuestMapFrame_GetDetailQuestID() return details.questID end

    local popup = CreateFrame('Frame', 'QuestLogPopupDetailFrame', UIParent)
    AddFooter(popup, 21, 403)
    popup.RewardsFrameContainer = false -- the permissive stub otherwise supplies a no-op function
    popup.ShowMapButton = CreateFrame('Button', nil, popup)
    popup:Hide()
    function popup:ShowQuest(id) self.questID = id; self:Show() end
end

-- CreateUI also registers HermesWoW's existing link hooks.
function hooksecurefunc() end
"""


class QuestButtonTest(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory(prefix="hermeswow-quest-"))
        self.addons = Path(tmp) / "AddOns"
        shutil.copytree(ROOT / "addon" / "HermesWoW", self.addons / "HermesWoW")
        self.sim = Sim(self.addons)
        self.H = self.sim.H
        self.lua = self.sim.lua
        self.g: Any = self.lua.globals()
        self.lua.execute(QUEST_API)
        self.sim.fire("ADDON_LOADED", "HermesWoW")

    def login(self, *, late=False):
        if not late:
            self.g.STUB.AddQuestPanels()
        self.sim.fire("PLAYER_LOGIN")
        if late:
            self.g.STUB.AddQuestPanels()
            self.sim.fire("ADDON_LOADED", "Blizzard_UIPanels_Game")
        self.lua.execute("""HermesWoW.ApplyInbox({ now = time(), tok = HermesWoW.db.token,
            gw = { state = 'connected' }, acks = { 1 } })""")

    def click(self, name):
        button = self.g[name]
        self.assertIsNotNone(button, f"missing quest detail button: {name}")
        self.assertIsNotNone(button._scripts.OnClick)
        button._scripts.OnClick(button)

    def choose(self, name, destination=None, *, session=None):
        self.click(name)
        menu = self.g.STUB.questMenu
        self.assertIsNotNone(menu, 'click must offer a destination before sending')
        if destination is not None:
            choice = menu[destination]
            if session is not None:
                choice = choice.children[session]
            self.assertIsNotNone(choice.click)
            choice.click()

    def test_button_is_its_own_full_width_native_footer_row(self):
        self.login()
        rawequal: Any = self.lua.eval('rawequal')
        for panel, name, height in (
            (self.g.QuestMapFrame.DetailsFrame, 'HermesWoWQuestMapAsk', 22),
            (self.g.QuestLogPopupDetailFrame, 'HermesWoWQuestPopupAsk', 21),
        ):
            button = self.g[name]
            self.assertEqual(button.GetHeight(button), height, name)
            anchors = button._anchors
            self.assertIsNotNone(anchors['BOTTOMLEFT'], name)
            self.assertTrue(rawequal(anchors['BOTTOMLEFT'][2], panel.AbandonButton))
            self.assertEqual(anchors['BOTTOMLEFT'][3], 'TOPLEFT')
            self.assertGreater(anchors['BOTTOMLEFT'][5], 0)
            self.assertTrue(rawequal(anchors['BOTTOMRIGHT'][2], panel.TrackButton))
            self.assertEqual(anchors['BOTTOMRIGHT'][3], 'TOPRIGHT')
            self.assertEqual(button.GetText(button), 'Preguntar a Hermes')
            self.assertEqual(panel.ScrollFrame.GetHeight(panel.ScrollFrame), (430 if height == 22 else 403) - height - 2)
        rewards = self.g.QuestMapFrame.DetailsFrame.RewardsFrameContainer
        self.assertEqual(rewards._anchors['BOTTOMLEFT'][5], 23 + 22 + 2)

    def test_menu_allows_explicit_new_without_switching_the_open_session(self):
        self.login()
        self.H.db.selected = 'work-session'
        self.g.QuestMapFrame_ShowQuestDetails(42)
        self.choose('HermesWoWQuestMapAsk')
        self.assertEqual(self.H.PendingRecords(), 0)
        self.assertEqual(self.g.objectiveReads, 0)
        self.g.STUB.questMenu['Nueva conversación'].click()
        op, target, mid, _, _ = self.one_send()
        self.assertEqual((op, target), ('new', ''))
        self.lua.execute(f"""HermesWoW.ApplyInbox({{ now = time(), tok = HermesWoW.db.token,
            acks = {{ {mid} }}, sends = {{ ['{mid}'] = {{ state = 'started', sid = 'quest-session' }} }} }})""")
        self.assertEqual(self.H.db.selected, 'work-session')
        self.assertEqual(self.H.db.sends[mid].sid, 'quest-session')
        self.assertEqual(self.H.PendingRecords(), 0)

    def test_menu_sends_to_the_named_session_not_the_current_one(self):
        self.login()
        self.H.db.selected = 'work-session'
        self.lua.execute("""HermesWoW.ApplyInbox({ now = time(), tok = HermesWoW.db.token,
            sessions = {{id = 'work-session', title = 'Trabajo', n = 1},
                {id = 'wow-session', title = 'WoW', n = 2}} })""")
        self.g.QuestMapFrame_ShowQuestDetails(42)
        self.choose('HermesWoWQuestMapAsk', 'Conversación existente', session='WoW')
        self.assertEqual(self.one_send()[:2], ('send', 'wow-session'))
        self.assertEqual(self.H.db.selected, 'work-session')

    def test_stale_menu_cannot_send_a_different_quest(self):
        self.login()
        self.g.QuestMapFrame_ShowQuestDetails(42)
        self.choose('HermesWoWQuestMapAsk')
        self.g.QuestMapFrame_ShowQuestDetails(43)
        self.g.STUB.questMenu['Nueva conversación'].click()
        self.assertEqual(self.H.PendingRecords(), 0)
        self.assertEqual(self.g.objectiveReads, 0)

    def one_send(self):
        frame = self.sim.strip_payload()
        assert frame is not None, "button must put a request on the actual pixel strip"
        records = hermeswow.parse_frame(frame[1])
        self.assertEqual(len(records), 1)
        tok, rid, op, target, mid, part, total, payload = records[0]
        self.assertEqual((tok, mid, part, total), (self.H.db.token, rid, '1', '1'))
        text, separator, extra = payload.partition(hermeswow.GS)
        self.assertTrue(separator, "context must travel separately from the transcript text")
        self.assertNotIn(hermeswow.GS, extra, "no screenshot flag")
        self.assertTrue(self.H.db.sends[int(mid)].quick)
        return op, target, int(mid), text, extra

    def test_map_click_captures_displayed_quest_and_prints_reply(self):
        self.login()
        self.H.db.selected = 'existing-wow-session'
        self.lua.execute("""HermesWoW.ApplyInbox({ now = time(), tok = HermesWoW.db.token,
            sessions = {{ id = 'existing-wow-session', title = 'WoW', n = 1 }} })""")
        self.H.db.context = True  # The quest button still sends only the scoped snapshot.
        self.H.ui.shotCheck.SetChecked(self.H.ui.shotCheck, True)
        self.g.QuestMapFrame_ShowQuestDetails(43)
        self.g.QuestMapFrame_ShowQuestDetails(42)
        self.assertEqual(self.g.objectiveReads, 0, 'do not gather context just by opening a quest')
        self.assertEqual(self.H.PendingRecords(), 0)
        self.choose('HermesWoWQuestMapAsk', 'Conversación existente', session='WoW')
        op, target, mid, text, extra = self.one_send()
        self.assertEqual((op, target), ('send', 'existing-wow-session'))
        self.assertIn('¿Qué me falta', text)
        self.assertIn('Colmillos del bosque', text)
        for value in ['42', 'Colmillos', '3/8', 'vigía', 'complete', 'Bosque de Elwynn', 'Villadorada', '42.0, 65.7']:
            self.assertIn(value, extra)
        for value in ['Entrega en Villadorada', '|cff', 'Quest log:', 'PrivateCharacter', 'PrivateRealm']:
            self.assertNotIn(value, extra)
        self.assertEqual(self.g.objectiveReads, 1)
        self.assertFalse(self.H.IsShown(), 'do not open a second window over the quest')

        # Run the real companion with display capture disabled and only its RPC peer replaced.
        slot = self.addons / 'HermesWoW_S001' / 'Inbox.lua'
        slot.parent.mkdir()
        slot.touch()
        bridge = hermeswow.Companion(SimpleNamespace(addons=self.addons, state=self.addons / 'state.json', inject=True))
        frame = self.sim.strip_payload()
        assert frame is not None
        records = hermeswow.parse_frame(frame[1])
        requests = []

        class Gateway:
            async def rpc(self, method, **kwargs):
                requests.append((method, kwargs))
                return {'status': 'started', 'admission_id': 'quest-admission'}

        async def round_trip():
            await bridge.handle_record(records[0])
            job, = bridge.store.data['jobs']
            view = bridge.views[target] = hermeswow.View(target)
            view.subscription = 'test-subscription'
            bridge.watch(target)
            bridge.client = Gateway()
            await bridge.run_job(job)
            # The real event handler queues a normal refresh; no further RPC needed here.
            bridge.on_event(view, 'message.complete', {'text': 'Te faltan cinco.'},
                            {'admission_id': 'quest-admission'})
            bridge.publish()

        asyncio.run(round_trip())
        (method, request), = requests
        self.assertEqual((method, request['session_id'], request['text']), ('prompt.submit', target, text))
        self.assertEqual(request['api_text'], extra + '\n\n' + text)
        for line in ('Player level: 23', 'Player class: Cazador', 'Player race: Enano', 'Player faction: Alliance'):
            self.assertIn(line, request['api_text'].splitlines())
        self.assertNotIn('attachments', request)
        self.H.LoadSlot()
        self.assertEqual(self.H.PendingRecords(), 0)
        self.assertTrue(self.H.db.sends[mid].printed)
        self.assertIn('Te faltan cinco.', list(self.g.STUB.chat.values())[-1])

    def test_popup_uses_its_own_selection_and_handles_missing_objectives_and_position(self):
        self.login(late=True)
        self.lua.execute("""GetLocale = nil; C_Map.GetPlayerMapPosition = function() return nil end;
            UnitClass = nil; UnitRace = nil; UnitFactionGroup = nil; UnitLevel = function() return -1 end""")
        self.g.QuestMapFrame_ShowQuestDetails(42)
        popup = self.g.QuestLogPopupDetailFrame
        popup.ShowQuest(popup, 43)
        self.choose('HermesWoWQuestPopupAsk', 'New conversation')
        op, target, _, text, extra = self.one_send()
        self.assertEqual((op, target), ('new', ''))
        self.assertIn('What do I still need', text)
        self.assertIn('Entrega en Villadorada', text)
        self.assertIn('Objectives unavailable', extra)
        self.assertIn('Quest is complete', extra)
        self.assertNotIn('Colmillos', extra)
        for field in ('coordinates', 'level', 'class', 'race', 'faction'):
            self.assertNotIn(f'Player {field}:', extra)

    def test_no_request_for_missing_quest_or_hidden_panel(self):
        self.login()
        self.g.QuestMapFrame_ShowQuestDetails(999)
        self.choose('HermesWoWQuestMapAsk', 'Nueva conversación')
        self.assertEqual(self.H.PendingRecords(), 0)
        self.g.QuestMapFrame_ShowQuestDetails(42)
        self.choose('HermesWoWQuestMapAsk')
        details = self.g.QuestMapFrame.DetailsFrame
        details.Hide(details)
        self.g.STUB.questMenu['Nueva conversación'].click()
        self.assertEqual(self.H.PendingRecords(), 0)
        self.assertEqual(self.g.objectiveReads, 0)

    def test_rejected_new_question_does_not_block_retry(self):
        self.login()
        self.g.QuestMapFrame_ShowQuestDetails(42)
        self.choose('HermesWoWQuestMapAsk', 'Nueva conversación')
        _, _, mid, _, _ = self.one_send()
        self.lua.execute(f"""HermesWoW.ApplyInbox({{ now = time(), tok = HermesWoW.db.token,
            acks = {{ {mid} }}, sends = {{ ['{mid}'] = {{ state = 'error', err = 'refused' }} }} }})
            local before = GetTime
            GetTime = function() return before() + 4 end
        """)
        self.choose('HermesWoWQuestMapAsk', 'Nueva conversación')
        self.assertEqual(self.one_send()[0], 'new')

    def test_completed_turn_without_reply_does_not_block_another_quest(self):
        self.login()
        self.H.db.selected = 'wow-session'
        def ask(qid):
            self.g.QuestMapFrame_ShowQuestDetails(qid)
            self.choose('HermesWoWQuestMapAsk', 'Conversación existente', session='WoW')

        self.lua.execute("""HermesWoW.ApplyInbox({ now = time(), tok = HermesWoW.db.token,
            sessions = {{ id = 'wow-session', title = 'WoW', n = 2, status = 'live' }} })""")
        ask(42)
        _, _, mid, _, _ = self.one_send()
        self.lua.execute(f"""HermesWoW.ApplyInbox({{ now = time(), tok = HermesWoW.db.token,
            acks = {{ {mid} }}, sends = {{ ['{mid}'] = {{ state = 'done', sid = 'wow-session' }} }},
            views = {{ ['wow-session'] = {{ running = false, msgs = {{}} }} }} }})""")
        ask(43)
        _, _, mid, text, _ = self.one_send()
        self.assertIn('Entrega en Villadorada', text)
        self.lua.execute(f"""HermesWoW.ApplyInbox({{ now = time(), tok = HermesWoW.db.token,
            acks = {{ {mid} }}, sends = {{ ['{mid}'] = {{ state = 'done', sid = 'wow-session' }} }},
            views = {{ ['wow-session'] = {{ running = false, msgs = {{
                {{ r = 'assistant', t = 'Una sola respuesta.', ts = time() }} }} }} }} }})""")
        self.assertEqual(sum('Una sola respuesta.' in line for line in self.g.STUB.chat.values()), 1)

    def test_queued_question_blocks_another_on_the_same_session(self):
        self.login()
        self.lua.execute("""HermesWoW.ApplyInbox({ now = time(), tok = HermesWoW.db.token,
            sessions = {{ id = 'wow-session', title = 'WoW', n = 2, status = 'live' }} })""")
        def ask(qid):
            self.g.QuestMapFrame_ShowQuestDetails(qid)
            self.choose('HermesWoWQuestMapAsk', 'Conversación existente', session='WoW')

        self.H.db.selected = 'wow-session'
        ask(42)
        _, _, mid, _, _ = self.one_send()
        self.lua.execute(f"""HermesWoW.ApplyInbox({{ now = time(), tok = HermesWoW.db.token,
            acks = {{ {mid} }}, sends = {{ ['{mid}'] = {{ state = 'queued', sid = 'wow-session' }} }},
            views = {{ ['wow-session'] = {{ running = false, msgs = {{}} }} }} }})""")
        ask(43)
        self.assertEqual(self.H.PendingRecords(), 0)
        self.assertEqual(self.g.objectiveReads, 1)

    def test_no_request_queues_behind_busy_session(self):
        self.login()
        self.H.db.selected = 'busy'
        self.lua.execute("""HermesWoW.ApplyInbox({ now = time(), tok = HermesWoW.db.token,
            sessions = {{ id = 'busy', title = 'Ocupada', n = 2, status = 'live' }},
            views = { busy = { running = true } } })""")
        self.g.QuestMapFrame_ShowQuestDetails(42)
        self.choose('HermesWoWQuestMapAsk', 'Conversación existente', session='Ocupada')
        self.assertEqual(self.H.PendingRecords(), 0)
        self.assertEqual(self.g.objectiveReads, 0)


if __name__ == '__main__':
    unittest.main()
