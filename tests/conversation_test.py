"""Offline regressions for the conversation renderer (real Lua 5.1, stub WoW widgets)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from sim import ROOT, Sim


class ConversationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="hermeswow-conversation-")
        self.addCleanup(tmp.cleanup)
        addons = Path(tmp.name)
        shutil.copytree(ROOT / "addon" / "HermesWoW", addons / "HermesWoW")
        self.sim = Sim(addons)
        self.lua = self.sim.lua
        self.sim.fire("ADDON_LOADED", "HermesWoW")
        self.sim.fire("PLAYER_LOGIN")
        self.lua.execute('''
            time = function() return 1000 end
            local H = HermesWoW
            metrics = { formats = 0, measurements = 0 }
            local format = H.Format
            H.Format = function(...)
                metrics.formats = metrics.formats + 1
                return format(...)
            end
            local createFrame = CreateFrame
            CreateFrame = function(...)
                local frame = createFrame(...)
                if frame._parent == H.ui.convContent then
                    local createFontString = frame.CreateFontString
                    frame.CreateFontString = function(self, ...)
                        local font = createFontString(self, ...)
                        local height = font.GetStringHeight
                        font.GetStringHeight = function(...)
                            metrics.measurements = metrics.measurements + 1
                            return height(...)
                        end
                        return font
                    end
                end
                return frame
            end
            H.db.selected = "chat"
            H.Toggle()
            function publish(rev, count, live, correction, sid)
                local msgs = {}
                for i = 1, count do
                    msgs[i] = { r = "assistant", t = "**Message " .. i .. "**" }
                end
                if correction then msgs[2].t = correction end
                local view = { loaded = true, title = "History", msgs = msgs,
                    running = live ~= nil, live = live and { text = live, since = 995 } }
                H.ApplyInbox({ rev = rev, now = time(), tok = H.db.token,
                    views = { [sid or "chat"] = view } })
            end
            function visibleText(index)
                return H.ui.blocks[index].body:GetText()
            end
        ''')
        self.publish = self.lua.globals()["publish"]
        self.visible_text = self.lua.globals()["visibleText"]

    def reset_counts(self):
        self.lua.execute("metrics.formats, metrics.measurements = 0, 0")

    def counts(self):
        return self.lua.eval("metrics.formats, metrics.measurements")

    def test_unrelated_inbox_update_does_not_relayout_history(self):
        self.reset_counts()
        self.publish(1, 160)
        self.assertEqual(self.counts(), (160, 160))
        self.reset_counts()
        self.publish(2, 160)
        self.assertIn("Message 160", self.visible_text(160))
        self.assertEqual(self.counts(), (0, 0))

    def test_streaming_updates_only_the_changed_tail(self):
        self.publish(1, 160, "**Starting**")
        self.reset_counts()
        self.publish(2, 160, "**Starting** to answer")
        self.assertIn("to answer", self.visible_text(162))
        self.assertEqual(self.counts(), (1, 1))
        self.reset_counts()
        self.publish(3, 161)
        self.assertIn("Message 161", self.visible_text(161))
        self.assertFalse(self.sim.H.ui.blocks[162].IsShown(self.sim.H.ui.blocks[162]))
        self.assertEqual(self.counts(), (1, 1))

    def test_corrections_resize_and_switch_refresh_the_right_blocks(self):
        self.publish(1, 4)
        self.reset_counts()
        self.publish(2, 4, None, "Corrected")
        self.assertIn("Corrected", self.visible_text(2))
        self.assertEqual(self.counts(), (3, 3))
        self.reset_counts()
        self.lua.execute("HermesWoW.ui.convScroll:SetWidth(320); HermesWoW.Render()")
        self.assertEqual(self.counts(), (4, 4))
        self.publish(3, 1, None, None, "other")
        self.sim.H.Open("other")
        self.assertIn("Message 1", self.visible_text(1))
        self.assertFalse(self.sim.H.ui.blocks[2].IsShown(self.sim.H.ui.blocks[2]))

    def test_unchanged_updates_do_not_queue_scroll_to_bottom(self):
        self.lua.execute('''
            local scroll = HermesWoW.ui.convScroll
            scroll.GetVerticalScrollRange = function() return 600 end
            scroll.SetVerticalScroll = function(self, y) self.y = y end
            scroll.GetVerticalScroll = function(self) return self.y or 0 end
            function flushScroll()
                local timers = STUB.timers
                STUB.timers = {}
                for _, callback in ipairs(timers) do callback() end
                return scroll:GetVerticalScroll()
            end
        ''')
        flush = self.lua.globals()["flushScroll"]
        self.publish(1, 60, "Starting")
        self.assertEqual(flush(), 600)
        self.publish(2, 60, "Starting")
        self.lua.execute("HermesWoW.ui.convScroll:SetVerticalScroll(200)")
        self.assertEqual(flush(), 200)
        self.publish(3, 60, "Starting to answer")
        self.assertEqual(flush(), 200)
        self.lua.execute("HermesWoW.ui.convScroll:SetVerticalScroll(600)")
        self.publish(4, 60, "Starting to answer now")
        self.lua.execute("HermesWoW.ui.convScroll.GetVerticalScrollRange = function() return 660 end")
        self.assertEqual(flush(), 660)

    def test_dismissing_identical_failed_sends_uses_current_click_action(self):
        self.lua.execute('''
            local H = HermesWoW
            for i = 1, 2 do
                H.db.sends[i] = { sid = "chat", state = "error", err = "offline",
                    t = 1000, text = "Retry me" }
            end
        ''')
        self.publish(1, 1)
        self.assertEqual(self.visible_text(2), "Retry me")
        self.assertEqual(self.visible_text(3), "Retry me")
        self.lua.execute('''
            local block = HermesWoW.ui.blocks[2]
            block:GetScript("OnClick")(block)
        ''')
        self.assertFalse(self.sim.H.ui.blocks[3].IsShown(self.sim.H.ui.blocks[3]))
        self.assertEqual(self.sim.H.ui.input.GetText(self.sim.H.ui.input), "Retry me")
        self.lua.execute('''
            local block = HermesWoW.ui.blocks[2]
            block:GetScript("OnClick")(block)
        ''')
        self.assertFalse(self.sim.H.ui.blocks[2].IsShown(self.sim.H.ui.blocks[2]))


if __name__ == "__main__":
    unittest.main()
