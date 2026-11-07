-- HermesWoW core: saved state, the transport to the companion, and the data the UI shows.
--
-- Out (game -> companion): unacknowledged records are drawn as a pixel strip in the top-left
-- corner (Codec.lua); the companion screen-captures it.
-- In (companion -> game): the companion writes its state into every HermesWoW_Sxxx
-- load-on-demand addon. Each can be loaded once per UI session, so loads are spent only when
-- a doorbell file (sig\b, sig\t) turns playable for PlaySoundFile. sig\h is the heartbeat.
-- /reload frees every slot again. SavedVariables are only written on reload/logout, so they
-- hold the UI state and the unsent queue, never live data.

local H = {}
HermesWoW = H
local Codec = HermesWoW_Codec

local VERSION = "1.1.0"
local SLOT_COUNT = 200
local CELL, CELLS_PER_ROW, MAX_ROWS = 4, 200, 48
local US, RS = "\31", "\30"
local PROTOCOL = "HW1"
local PART_BYTES = 2400
H.MAX_MESSAGE = 16000
local SIG = "Interface\\AddOns\\HermesWoW\\sig\\"
local SIG_MAX = { b = 3000, t = 3000, h = 3000 }
local ONLINE_WINDOW = 75        -- seconds without a heartbeat before the companion counts as gone

local db
local run = {
	sig = { b = 0, t = 0, h = 0 },
	slotNext = 1,
	slotsUsed = 0,
	loads = 0,
	lastLoad = -1000,
	views = {},
}
H.run = run

---------------------------------------------------------------------------
-- Helpers
---------------------------------------------------------------------------

local function Now() return GetTime() end

-- Text from Hermes is data: "|" starts WoW escape codes (colors, links, textures).
function H.Escape(s)
	if type(s) ~= "string" then return "" end
	return (s:gsub("|", "||"))
end

local function Wire(s)
	s = tostring(s or "")
	return (s:gsub("[\30\31]", " "))
end

function H.Print(msg)
	DEFAULT_CHAT_FRAME:AddMessage("|cffffd100Hermes:|r " .. msg)
end

local function NewToken()
	local chars = "abcdefghijklmnopqrstuvwxyz0123456789"
	-- The time prefix keeps a re-created token from colliding with an older one, whose
	-- message ids Hermes would treat as already-submitted (submission_id idempotency).
	local t = { string.format("%08x", time()) }
	for i = 2, 13 do
		local k = math.random(1, #chars)
		t[i] = chars:sub(k, k)
	end
	return table.concat(t)
end

local function IsLoaded(name)
	if C_AddOns and C_AddOns.IsAddOnLoaded then return C_AddOns.IsAddOnLoaded(name) end
	return IsAddOnLoaded(name)
end

local function LoadAddOnByName(name)
	if C_AddOns and C_AddOns.LoadAddOn then return C_AddOns.LoadAddOn(name) end
	return LoadAddOn(name)
end

-- Split UTF-8 text into pieces of at most n bytes without cutting a character.
local function SplitUtf8(s, n)
	local parts, i = {}, 1
	while i <= #s do
		local j = math.min(i + n - 1, #s)
		while j < #s and j > i do
			local b = s:byte(j + 1)
			if b < 0x80 or b >= 0xC0 then break end
			j = j - 1
		end
		parts[#parts + 1] = s:sub(i, j)
		i = j + 1
	end
	if #parts == 0 then parts[1] = "" end
	return parts
end
H.SplitUtf8 = SplitUtf8

---------------------------------------------------------------------------
-- Saved state
---------------------------------------------------------------------------

local function InitDB()
	HermesWoWDB = HermesWoWDB or {}
	db = HermesWoWDB
	db.token = db.token or NewToken()
	db.nextId = db.nextId or 1
	db.outbox = db.outbox or {}
	db.sends = db.sends or {}
	db.seen = db.seen or {}
	db.drafts = db.drafts or {}
	db.cache = db.cache or {}
	db.rows = db.rows or {}
	db.notify = db.notify or { sound = true, toast = true }
	db.minimap = db.minimap or { angle = 200, hide = false }
	db.combat = db.combat or "hide"   -- what the window does in combat: "hide", "fade" or "off"
	H.db = db
	for _, rec in ipairs(db.outbox) do rec.shownAt = nil end  -- GetTime() values from a previous session
	-- Sends older than a day that never settled are history, not pending work.
	for mid, s in pairs(db.sends) do
		if (s.t or 0) < time() - 86400 then db.sends[mid] = nil end
	end
end

---------------------------------------------------------------------------
-- Pixel strip (out)
---------------------------------------------------------------------------

local strip
local cells = {}

local function EnsureStrip()
	if strip then return strip end
	strip = CreateFrame("Frame", "HermesWoWStrip", UIParent)
	strip:SetFrameStrata("TOOLTIP")
	strip:SetFrameLevel(9000)
	-- One UI unit = one physical pixel, independent of UI scale.
	if strip.SetIgnoreParentScale then strip:SetIgnoreParentScale(true) end
	local _, physH = GetPhysicalScreenSize()
	strip:SetScale(768 / (physH or 1080))
	strip:SetPoint("TOPLEFT", UIParent, "TOPLEFT", 0, 0)
	strip:SetSize(CELLS_PER_ROW * CELL, MAX_ROWS * CELL)
	strip:Hide()
	return strip
end

local function DrawStrip(id, payload)
	local values = Codec.Encode(id % 65536, payload)
	local s = EnsureStrip()
	local total = math.ceil(#values / CELLS_PER_ROW) * CELLS_PER_ROW
	for i = 1, total do
		local t = cells[i]
		if not t then
			t = s:CreateTexture(nil, "OVERLAY")
			t:SetSize(CELL, CELL)
			t:SetPoint("TOPLEFT", s, "TOPLEFT", ((i - 1) % CELLS_PER_ROW) * CELL, -math.floor((i - 1) / CELLS_PER_ROW) * CELL)
			cells[i] = t
		end
		t:SetColorTexture(Codec.CellColor(values[i] or 0))
		t:Show()
	end
	for i = total + 1, #cells do cells[i]:Hide() end
	s:Show()
end

local function RecordString(rec)
	local fields = { db.token, tostring(rec.id), rec.op }
	for _, f in ipairs(rec.f or {}) do fields[#fields + 1] = Wire(f) end
	return table.concat(fields, US)
end

-- Draw every unacknowledged record that fits, oldest first.
function H.RefreshStrip()
	local parts, size = { PROTOCOL }, #PROTOCOL
	local first
	for _, rec in ipairs(db.outbox) do
		local r = RecordString(rec)
		if size + #r + 1 > Codec.MAX_PAYLOAD then break end
		parts[#parts + 1] = r
		size = size + #r + 1
		first = first or rec.id
		rec.shownAt = rec.shownAt or Now()
	end
	if not first then
		if strip then strip:Hide() end
		run.stripShown = false
		return
	end
	DrawStrip(first, table.concat(parts, RS))
	run.stripShown = true
end

function H.Enqueue(op, fields)
	local id = db.nextId
	db.nextId = id + 1
	db.outbox[#db.outbox + 1] = { id = id, op = op, f = fields, t = time() }
	H.RefreshStrip()
	return id
end

local function DropAcked(acks)
	if type(acks) ~= "table" then return end
	local set = {}
	for _, id in ipairs(acks) do set[id] = true end
	local kept, changed = {}, false
	for _, rec in ipairs(db.outbox) do
		if set[rec.id] then changed = true else kept[#kept + 1] = rec end
	end
	if changed then
		db.outbox = kept
		H.RefreshStrip()
	end
end

function H.PendingRecords() return #db.outbox end

---------------------------------------------------------------------------
-- Signals (sound-file doorbells)
---------------------------------------------------------------------------

local function Playable(path)
	local ok, willPlay, handle = pcall(PlaySoundFile, path, "Master")
	if not ok then return false end
	if willPlay and handle then pcall(StopSound, handle) end
	return willPlay and true or false
end

local function SelfTest()
	if type(PlaySoundFile) ~= "function" then
		run.signals, run.signalWhy = false, "PlaySoundFile is missing"
	elseif Playable(SIG .. "ctl\\empty.wav") then
		run.signals, run.signalWhy = false, "an empty file reports as playable"
	elseif not Playable(SIG .. "ctl\\valid.wav") then
		run.signals, run.signalWhy = false, "the test sound does not play (Master sound off, or the game was not restarted after install)"
	else
		run.signals, run.signalWhy = true, nil
	end
end

-- Advance a doorbell counter past every file the companion has raised. True if it moved.
local function Scan(kind)
	local n, moved = run.sig[kind], false
	for _ = 1, 40 do
		if n >= SIG_MAX[kind] then break end
		if not Playable(string.format("%s%s\\%04d.wav", SIG, kind, n + 1)) then break end
		n, moved = n + 1, true
	end
	run.sig[kind] = n
	return moved
end

---------------------------------------------------------------------------
-- Slots (in)
---------------------------------------------------------------------------

local function SlotName(i) return string.format("HermesWoW_S%03d", i) end

function H.SlotsLeft()
	return SLOT_COUNT - run.slotsUsed
end

local function LoadSlot()
	local i = run.slotNext
	while i <= SLOT_COUNT and IsLoaded(SlotName(i)) do i = i + 1 end
	if i > SLOT_COUNT then
		run.slotsExhausted = true
		run.slotsUsed = SLOT_COUNT
		H.Changed()
		return
	end
	HermesWoW_Inbox = nil
	local loaded, why = LoadAddOnByName(SlotName(i))
	run.slotNext = i + 1
	run.slotsUsed = i
	run.lastLoad = Now()
	run.loads = run.loads + 1
	if not loaded then
		run.slotError = tostring(why)
		if why == "MISSING" or why == "DISABLED" or why == "INTERFACE_VERSION" then run.slotsBroken = why end
		H.Changed()
		return
	end
	run.slotError, run.slotsBroken = nil, nil
	local data = HermesWoW_Inbox
	HermesWoW_Inbox = nil
	if type(data) == "table" then
		H.ApplyInbox(data)
	else
		run.emptySlot = true
		H.Changed()
	end
end
H.LoadSlot = LoadSlot

-- Ask for a slot load no sooner than `gap` seconds after the previous one.
local function WantLoad(gap)
	gap = gap or 1
	if not run.loadAt or run.loadAt > run.lastLoad + gap then
		run.loadAt = math.max(Now(), run.lastLoad + gap)
	end
end
H.WantLoad = WantLoad

---------------------------------------------------------------------------
-- Inbox
---------------------------------------------------------------------------

local function CompanionClock(data)
	-- The companion and the game share the machine clock: when was this written, in GetTime()?
	return Now() - (time() - (data.now or 0))
end

function H.ApplyInbox(data)
	run.inbox = data
	run.emptySlot = nil
	run.companionSeen = math.max(run.companionSeen or -1000, CompanionClock(data))
	local sig = data.sig
	if type(sig) == "table" then
		if sig.epoch ~= run.sig.epoch then
			run.sig = { b = sig.b or 0, t = sig.t or 0, h = sig.h or 0, epoch = sig.epoch }
		else
			for _, k in ipairs({ "b", "t", "h" }) do run.sig[k] = math.max(run.sig[k], sig[k] or 0) end
		end
	end
	if data.tok == db.token then
		local before = #db.outbox
		DropAcked(data.acks)
		if #db.outbox < before then run.idleLoads = 0 end
		if type(data.sends) == "table" then
			for key, st in pairs(data.sends) do
				local mid = tonumber(key)
				local s = mid and db.sends[mid]
				if s then
					s.state, s.err = st.state, st.err
					if s.isNew and not s.sid and st.sid and st.sid ~= "" then
						s.sid = st.sid
						if run.awaitNew == mid then
							run.awaitNew = nil
							H.Open(st.sid)
						end
					end
					if s.shot == "wanted" and st.shot then
						s.shot = st.shot
						if st.shot == "failed" then
							H.Print("could not take the screenshot (" .. H.Escape(st.shot_err or "unknown error") .. "); the message went without it.")
						end
						if H.ShotDone then H.ShotDone() end
					end
				end
			end
		end
	end
	if type(data.sessions) == "table" then
		db.cache.sessions = data.sessions
		H.UpdateUnread(data.sessions)
	end
	if type(data.views) == "table" then
		run.promptsSeen = run.promptsSeen or {}
		for sid, v in pairs(data.views) do
			run.views[sid] = v
			for _, p in ipairs(v.prompts or {}) do
				if p.id and not run.promptsSeen[p.id] then
					run.promptsSeen[p.id] = true
					if H.Notify then
						H.Notify("prompt", sid, v.title or "", p.kind == "approval" and "asks for approval" or "has a question")
					end
				end
			end
		end
		local sel = db.selected and data.views[db.selected]
		if sel then db.cache.view = { sid = db.selected, data = sel } end
	end
	H.PrintQuickReplies()
	H.Changed()
end

function H.View(sid)
	if not sid then return nil end
	local v = run.views[sid]
	if v then return v end
	if db.cache.view and db.cache.view.sid == sid then return db.cache.view.data, true end
end

function H.Sessions()
	return (run.inbox and run.inbox.sessions) or db.cache.sessions or {}
end

-- { cur = model, prov = provider, list = { { p = slug, n = name, m = { models } } } } or nil.
function H.Models()
	return run.inbox and run.inbox.models
end

function H.SessionRow(sid)
	for _, s in ipairs(H.Sessions()) do
		if s.id == sid then return s end
	end
end

function H.IsUnread(sid)
	local row = H.SessionRow(sid)
	return row and db.seen[sid] and row.n > db.seen[sid] or false
end

function H.UpdateUnread(sessions)
	local shown = H.IsShown and H.IsShown()
	for _, s in ipairs(sessions) do
		local seen = db.seen[s.id]
		if seen == nil then
			db.seen[s.id] = s.n            -- first sight: nothing is "new" yet
		elseif shown and s.id == db.selected then
			db.seen[s.id] = s.n
		elseif s.n > seen and s.status ~= "running" and not (run.notified and run.notified[s.id] == s.n) then
			run.notified = run.notified or {}
			run.notified[s.id] = s.n
			-- A quick question's reply is printed in full by PrintQuickReplies instead.
			if not shown and not H.QuickPending(s.id) then
				H.Print("new reply in |cffffffff" .. H.Escape(s.title ~= "" and s.title or "Untitled") .. "|r - type /hermes to read it.")
			end
			if H.Notify then H.Notify("reply", s.id, s.title) end
		end
	end
end

function H.MarkSeen(sid)
	local row = H.SessionRow(sid)
	if row then db.seen[sid] = row.n end
end

---------------------------------------------------------------------------
-- Status
---------------------------------------------------------------------------

-- Returns level ("ok", "warn", "error"), a short text and detail lines for the tooltip.
function H.Status()
	local now = Now()
	local seen = math.max(run.lastBeat or -1000, run.companionSeen or -1000)
	local window = run.signals and ONLINE_WINDOW or 900
	local detail = {}
	local level, text = "ok", "Connected"
	if run.slotsBroken then
		level, text = "error", "Reply slots unavailable (" .. run.slotsBroken .. ")"
		detail[#detail + 1] = "Run companion\\install.py, enable the HermesWoW slot addons and restart the game."
	elseif not run.inbox and not run.companionSeen then
		level, text = "error", "Companion not running"
		detail[#detail + 1] = "Start companion\\start.ps1 (see README)."
	elseif now - seen > window then
		level, text = "error", "Companion not responding"
		detail[#detail + 1] = string.format("Last heard %d s ago. Is start.ps1 still running?", now - seen)
	else
		local gw = run.inbox and run.inbox.gw or {}
		if gw.state == "offline" then
			level, text = "error", "Hermes gateway offline"
			if gw.detail and gw.detail ~= "" then detail[#detail + 1] = gw.detail end
		elseif gw.state == "connecting" then
			level, text = "warn", "Connecting to Hermes..."
		end
	end
	if run.signals == false then
		if level == "ok" then level = "warn" end
		detail[#detail + 1] = "Doorbells off: " .. (run.signalWhy or "?") .. ". Using slower timed polling."
	end
	if run.slotsExhausted then
		level, text = "error", "Reload needed"
		detail[#detail + 1] = "All reply slots are used. Click Reload (or /reload) to continue."
	end
	detail[#detail + 1] = string.format("Reply slots left this UI session: %d / %d", H.SlotsLeft(), SLOT_COUNT)
	local pend = #db.outbox
	if pend > 0 then
		local oldest = db.outbox[1].shownAt
		detail[#detail + 1] = string.format("%d record(s) waiting for the companion%s", pend,
			oldest and string.format(" (%d s)", now - oldest) or "")
	end
	return level, text, detail
end

---------------------------------------------------------------------------
-- Actions
---------------------------------------------------------------------------

function H.Open(sid, force)
	if not sid then return end
	db.selected = sid
	local rows = db.rows[sid] or 40
	-- Only the newest open request matters.
	for i = #db.outbox, 1, -1 do
		if db.outbox[i].op == "open" then table.remove(db.outbox, i) end
	end
	H.Enqueue("open", { sid, tostring(rows) })
	H.MarkSeen(sid)
	H.Changed()
end

function H.LoadOlder(sid)
	db.rows[sid] = math.min((db.rows[sid] or 40) + 40, 400)
	H.Open(sid)
end

function H.Refresh()
	H.Enqueue("list", {})
	if db.selected then H.Open(db.selected) end
end

-- Send text to a session, or to a new session when sid is nil. Returns the message id or nil, why.
---------------------------------------------------------------------------
-- Game context and links (sent to the model only, never shown in the transcript)
---------------------------------------------------------------------------

local GS = "\29"   -- separates the message from model-only text in a send payload

local function NoCodes(s)
	s = tostring(s or "")
	s = s:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|T.-|t", ""):gsub("|A.-|a", "")
	return (s:gsub("|", ""):gsub("[%c]", " "))
end

-- A few lines about the character and surroundings. Every query is guarded, so an API this
-- client lacks just leaves its line out.
function H.GameContext()
	local lines = {}
	local function try(fn)
		local ok, line = pcall(fn)
		if ok and line and line ~= "" then lines[#lines + 1] = NoCodes(line) end
	end
	try(function()
		return string.format("Character: %s (%s), level %d %s %s", UnitName("player"), GetRealmName(),
			UnitLevel("player"), UnitRace("player") or "", UnitClass("player") or "")
	end)
	try(function()
		local zone, sub = GetRealZoneText(), GetSubZoneText()
		return "Zone: " .. zone .. ((sub and sub ~= "" and sub ~= zone) and (" - " .. sub) or "")
	end)
	try(function()
		local map = C_Map.GetBestMapForUnit("player")
		local pos = map and C_Map.GetPlayerMapPosition(map, "player")
		if pos then
			local x, y = pos:GetXY()
			return string.format("Map coordinates: %.1f, %.1f", x * 100, y * 100)
		end
	end)
	try(function()
		if not UnitExists("target") then return nil end
		local level = UnitLevel("target")
		local kind = UnitIsPlayer("target") and (UnitClass("target") or "player") or (UnitCreatureType("target") or "")
		local class = UnitClassification("target")
		local reaction = UnitIsEnemy("player", "target") and "hostile" or (UnitIsFriend("player", "target") and "friendly" or "neutral")
		local hp = UnitHealthMax("target") > 0 and math.floor(UnitHealth("target") / UnitHealthMax("target") * 100) or 0
		return string.format("Target: %s, level %s %s%s, %s, %d%% health%s", UnitName("target"),
			level == -1 and "??" or tostring(level), kind, (class and class ~= "normal") and (" (" .. class .. ")") or "",
			reaction, hp, UnitIsDead("target") and ", dead" or "")
	end)
	try(function()
		local hp = math.floor(UnitHealth("player") / UnitHealthMax("player") * 100)
		local mp = UnitPowerMax("player") > 0 and math.floor(UnitPower("player") / UnitPowerMax("player") * 100) or nil
		return string.format("Health %d%%%s%s", hp, mp and string.format(", power %d%%", mp) or "",
			UnitAffectingCombat("player") and ", in combat" or "")
	end)
	try(function() return "Money: " .. GetCoinText(GetMoney(), " ") end)
	try(function()
		if IsInRaid() then return "Group: raid of " .. GetNumGroupMembers() end
		if IsInGroup() then return "Group: party of " .. GetNumGroupMembers() end
	end)
	try(function()
		local quests = {}
		if C_QuestLog and C_QuestLog.GetNumQuestLogEntries and C_QuestLog.GetInfo then
			for i = 1, C_QuestLog.GetNumQuestLogEntries() do
				local q = C_QuestLog.GetInfo(i)
				if q and not q.isHeader and q.title then
					local done = q.questID and C_QuestLog.IsComplete and C_QuestLog.IsComplete(q.questID)
					quests[#quests + 1] = q.title .. (done and " (complete)" or "")
				end
			end
		elseif GetNumQuestLogEntries then
			for i = 1, GetNumQuestLogEntries() do
				local title, level, _, isHeader, _, isComplete = GetQuestLogTitle(i)
				if title and not isHeader then
					quests[#quests + 1] = string.format("[%d] %s%s", level or 0, title, isComplete == 1 and " (complete)" or "")
				end
			end
		end
		if #quests > 0 then return "Quest log: " .. table.concat(quests, "; ", 1, math.min(#quests, 25)) end
	end)
	local text = table.concat(lines, "\n")
	if #text > 1500 then text = text:sub(1, 1500) end
	return text
end

local scanTip
local function TooltipLines(data)
	if not scanTip then
		scanTip = CreateFrame("GameTooltip", "HermesWoWScanTip", UIParent, "GameTooltipTemplate")
	end
	scanTip:SetOwner(UIParent, "ANCHOR_NONE")
	scanTip:ClearLines()
	if not pcall(scanTip.SetHyperlink, scanTip, data) then return {} end
	local out = {}
	for i = 2, math.min(scanTip:NumLines() or 0, 25) do
		local left = _G["HermesWoWScanTipTextLeft" .. i]
		local right = _G["HermesWoWScanTipTextRight" .. i]
		local l = left and left:GetText()
		local r = right and right:IsShown() and right:GetText()
		if l and l ~= "" then out[#out + 1] = NoCodes(l) .. (r and r ~= "" and ("    " .. NoCodes(r)) or "") end
	end
	scanTip:Hide()
	return out
end

-- Edit box text -> (message as typed, with links reduced to "[Name]"; tooltip text of each
-- link for the model, or nil). The edit box escapes a typed "|" as "||".
function H.ExpandLinks(raw)
	local s = (raw or ""):gsub("||", "\2")
	local blocks = {}
	s = s:gsub("|c%x%x%x%x%x%x%x%x(|H.-|h.-|h)|r", "%1")
	s = s:gsub("|H(.-)|h(.-)|h", function(data, label)
		local name = NoCodes(label)
		local kind = data:match("^([^:]+)") or "link"
		local tip = TooltipLines(data)
		blocks[#blocks + 1] = name .. " (" .. kind .. " " .. (data:match("^[^:]+:(%-?%d+)") or "?") .. ")"
			.. (#tip > 0 and ("\n  " .. table.concat(tip, "\n  ")) or "")
		return name
	end)
	s = s:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|T.-|t", "")
	s = s:gsub("\2", "|"):gsub(GS, "")
	return s, #blocks > 0 and ("Linked from the game (tooltip text):\n" .. table.concat(blocks, "\n")) or nil
end

-- The model-only text for a message: linked tooltips and, if enabled, game context.
function H.ModelExtras(links)
	local parts = {}
	if db.context then
		parts[#parts + 1] = "[Game context from the HermesWoW addon: the user is playing World of Warcraft: Forever "
			.. "(a Classic-era client) and wrote this message from inside the game.]\n" .. H.GameContext()
	end
	if links then parts[#parts + 1] = links end
	return #parts > 0 and table.concat(parts, "\n\n") or nil
end

-- Send text to a session, or to a new session when sid is nil (model = { p = provider, m = model }
-- or nil for the profile default). `extra` reaches the model only. opts.shot asks the companion
-- to attach a screenshot of the game; opts.quick prints the reply in chat. Returns the id or nil, why.
function H.Send(sid, text, extra, model, opts)
	opts = opts or {}
	text = (text or ""):gsub("\r", ""):gsub(GS, "")
	text = text:gsub("^%s+", ""):gsub("%s+$", "")
	if text == "" then return nil, "empty" end
	-- Payload: message <GS> model-only text <GS> flags.
	local payload = text
	if extra or opts.shot then payload = payload .. GS .. (extra or ""):gsub(GS, "") end
	if opts.shot then payload = payload .. GS .. "shot" end
	if #payload > H.MAX_MESSAGE then return nil, "too long" end
	local last = run.lastSend
	if last and last.sid == sid and last.text == text and Now() - last.at < 3 then
		return nil, "duplicate"
	end
	local parts = SplitUtf8(payload, PART_BYTES)
	local mid = db.nextId
	-- A new session's first field carries the chosen model ("provider<GS>model", "" = default).
	local target = sid or (model and model.m and ((model.p or "") .. GS .. model.m)) or ""
	for i, p in ipairs(parts) do
		H.Enqueue(sid and "send" or "new", { target, tostring(mid), tostring(i), tostring(#parts), p })
	end
	db.sends[mid] = { sid = sid, text = text, state = "sending", t = time(), isNew = not sid, parts = #parts,
		shot = opts.shot and "wanted" or nil, quick = opts.quick or nil }
	run.lastSend = { sid = sid, text = text, at = Now() }
	if not sid then run.awaitNew = mid end
	H.Changed()
	return mid
end

-- Ask from the chat line (/hermes ask, /hermes look): goes to the open conversation, or starts a
-- new one; the reply is printed in chat.
function H.QuickAsk(raw, shot)
	local text, links = H.ExpandLinks(raw)
	local sid = db.selected
	local mid, why = H.Send(sid, text, H.ModelExtras(links), not sid and db.newModel or nil, { shot = shot, quick = true })
	if not mid then
		if why == "empty" then
			H.Print("usage: /hermes " .. (shot and "look" or "ask") .. " <question>")
		elseif why == "too long" then
			H.Print(string.format("message too long (limit %d bytes).", H.MAX_MESSAGE))
		end
		return
	end
	local row = sid and H.SessionRow(sid)
	H.Print((shot and "asked with a screenshot" or "asked") .. " in |cffffffff"
		.. H.Escape(row and row.title ~= "" and row.title or (sid and "the open conversation" or "a new conversation"))
		.. "|r. The reply will appear here.")
	if shot and H.HideForShot then H.HideForShot(mid) end
end

-- True while a quick question to this session waits for its reply.
function H.QuickPending(sid)
	for _, s in pairs(db.sends) do
		if s.quick and not s.printed and s.sid == sid then return true end
	end
	return false
end

-- Reply text for the chat frame: Markdown markers dropped, "|" escaped, a few lines at most.
local function ChatLines(text, max)
	local out = {}
	for line in (text .. "\n"):gmatch("([^\n]*)\n") do
		line = line:gsub("^%s*```.*$", ""):gsub("^#+%s*", ""):gsub("%*%*", ""):gsub("`", "")
		line = line:gsub("^%s*[-*]%s+", "  - ")
		if line:match("%S") then
			while #line > 0 and #out < max do
				local piece = SplitUtf8(line, 240)[1]
				if #line > #piece then piece = piece:match("^(.*%s)%S*$") or piece end
				out[#out + 1] = H.Escape(piece)
				line = line:sub(#piece + 1)
			end
		end
		if #out >= max then break end
	end
	return out
end
H.ChatLines = ChatLines

function H.PrintQuickReplies()
	for _, s in pairs(db.sends) do
		if s.quick and not s.printed and s.sid then
			local v = run.views[s.sid]
			local last = v and v.msgs and v.msgs[#v.msgs]
			if s.state == "error" then
				s.printed = true
				H.Print("|cffff4040could not send:|r " .. H.Escape(s.err or "unknown error"))
			elseif v and not v.running and last and last.r == "assistant" and (last.ts or 0) >= (s.t or 0) then
				s.printed = true
				if not (H.IsShown and H.IsShown() and db.selected == s.sid) then
					local lines = ChatLines(last.t or "", 8)
					local total = #ChatLines(last.t or "", 9)
					DEFAULT_CHAT_FRAME:AddMessage("|cffffd100Hermes:|r " .. (lines[1] or ""))
					for i = 2, #lines do DEFAULT_CHAT_FRAME:AddMessage("  " .. lines[i]) end
					if total > #lines then DEFAULT_CHAT_FRAME:AddMessage("  |cff808080... type /hermes to read the rest.|r") end
				end
			end
		end
	end
end

-- Session list edits. The list row changes right away; the companion's next list confirms it.
function H.Pin(sid, on)
	local row = H.SessionRow(sid)
	if row then row.pin = on end
	H.Enqueue("pin", { sid, on and "1" or "0" })
	H.Changed()
end

function H.Rename(sid, title)
	title = (title or ""):gsub("[%c]", " "):gsub("^%s+", ""):gsub("%s+$", "")
	if title == "" then return end
	local row = H.SessionRow(sid)
	if row then row.title = title end
	local v = run.views[sid]
	if v then v.title = title end
	H.Enqueue("rename", { sid, title })
	H.Changed()
end

function H.Stop(sid) H.Enqueue("stop", { sid }) H.Changed() end
function H.Approve(sid, promptId, choice) H.Enqueue("approve", { sid, promptId, choice }) H.Changed() end
function H.Answer(sid, promptId, text) H.Enqueue("answer", { sid, promptId, text }) H.Changed() end

-- Once Hermes admitted a message the session's own transcript/queue shows it.
local ADMITTED = { queued = true, started = true, done = true }

-- Sends for a session (nil = the new-session draft) that Hermes has not admitted yet,
-- plus failed ones until dismissed.
function H.PendingSends(sid)
	local list = {}
	for mid, s in pairs(db.sends) do
		if s.sid == sid and not ADMITTED[s.state] and not s.dismissed then list[#list + 1] = { mid = mid, s = s } end
	end
	table.sort(list, function(a, b) return a.mid < b.mid end)
	return list
end

function H.Dismiss(mid)
	if db.sends[mid] then db.sends[mid].dismissed = true end
	H.Changed()
end

function H.Busy(sid)
	local v = H.View(sid)
	if v and v.running then return true end
	for _, p in ipairs(H.PendingSends(sid)) do
		if p.s.state ~= "error" then return true end
	end
	return false
end

---------------------------------------------------------------------------
-- Driver
---------------------------------------------------------------------------

function H.Changed()
	if H.OnChanged then H.OnChanged() end
	if H.UpdateMinimap then H.UpdateMinimap() end
end

local lastBellScan, lastBeatScan, lastTickScan = 0, 0, 0

local function Tick()
	local now = Now()
	local signals = run.signals
	if signals then
		if now - lastBellScan >= 0.5 then
			lastBellScan = now
			if Scan("b") then WantLoad(0.75) end
		end
		if now - lastBeatScan >= 2 then
			lastBeatScan = now
			if Scan("h") then run.lastBeat = now H.Changed() end
		end
		local watching = H.IsShown and H.IsShown() and db.selected and H.Busy(db.selected)
		if watching and now - lastTickScan >= 1 then
			lastTickScan = now
			if Scan("t") then WantLoad(3) end
		end
	end
	-- Safety net for missed doorbells, and the only path when doorbells are off. Every load
	-- spends a slot, so back off while loads bring nothing and the companion seems gone.
	local pending = #db.outbox > 0
	local busy = H.Busy(db.selected)
	local sinceLoad = now - run.lastLoad
	local alive = signals and run.lastBeat and now - run.lastBeat < ONLINE_WINDOW
	local backoff = math.min(1.6 ^ (run.idleLoads or 0), 40)
	local gap
	if pending then
		gap = (signals and (alive and 8 or 120) or 4) * backoff
	elseif busy then
		run.busySince = run.busySince or now
		gap = signals and 45 or math.min(6 + (now - run.busySince) / 10, 30)
	elseif not signals then
		gap = (H.IsShown and H.IsShown()) and 120 or 600
	elseif not alive then
		-- No heartbeat: the companion is down, or restarted its doorbells (new epoch) while we
		-- watch old counters. An occasional load resyncs either way.
		gap = (H.IsShown and H.IsShown()) and 90 or 600
	end
	if not busy then run.busySince = nil end
	local age = pending and (now - (db.outbox[1].shownAt or now)) or math.huge
	gap = gap and math.min(gap, 900)
	if gap and sinceLoad > gap and age > gap and not run.loadAt then
		if pending then run.idleLoads = (run.idleLoads or 0) + 1 end
		WantLoad(0)
	end
	if run.loadAt and now >= run.loadAt and not run.slotsExhausted and not run.slotsBroken then
		run.loadAt = nil
		LoadSlot()
	end
	if H.OnTick then H.OnTick() end
end

local events = CreateFrame("Frame")
events:RegisterEvent("ADDON_LOADED")
events:RegisterEvent("PLAYER_LOGIN")
events:RegisterEvent("PLAYER_REGEN_DISABLED")
events:RegisterEvent("PLAYER_REGEN_ENABLED")
events:SetScript("OnEvent", function(_, event, name)
	if event == "ADDON_LOADED" and name == "HermesWoW" then
		InitDB()
	elseif event == "PLAYER_LOGIN" then
		SelfTest()
		if H.CreateUI then H.CreateUI() end
		if H.CreateNotify then H.CreateNotify() end
		H.Enqueue("hello", { VERSION })
		if db.selected then H.Open(db.selected) end
		WantLoad(0)
		C_Timer.NewTicker(0.25, Tick)
	elseif event == "PLAYER_REGEN_DISABLED" or event == "PLAYER_REGEN_ENABLED" then
		run.inCombat = event == "PLAYER_REGEN_DISABLED"
		if H.OnCombat then H.OnCombat(run.inCombat) end
	end
end)

function H.InCombat() return run.inCombat and db.combat ~= "off" or false end

local COMBAT_MODES = {
	hide = "the window hides in combat and comes back after it; sounds and toasts wait until combat ends",
	fade = "the window fades in combat; sounds and toasts wait until combat ends",
	off = "nothing changes in combat",
}

SLASH_HERMESWOW1 = "/hermes"
SlashCmdList.HERMESWOW = function(raw)
	raw = (raw or ""):gsub("^%s+", ""):gsub("%s+$", "")
	local cmd, rest = raw:match("^(%S*)%s*(.-)$")
	cmd = cmd:lower()
	local msg = raw:lower()
	if cmd == "ask" or cmd == "look" then
		H.QuickAsk(rest, cmd == "look")
	elseif cmd == "combat" then
		local mode = rest:lower()
		if COMBAT_MODES[mode] then db.combat = mode end
		H.Print("combat mode: " .. db.combat .. " - " .. COMBAT_MODES[db.combat] .. ".")
	elseif msg == "status" then
		local level, text, detail = H.Status()
		H.Print(text .. " (" .. level .. ")")
		for _, l in ipairs(detail) do H.Print("  " .. l) end
		H.Print(string.format("  doorbells b=%d t=%d h=%d, loads=%d", run.sig.b, run.sig.t, run.sig.h, run.loads))
	elseif msg == "reset" then
		if H.ResetPosition then H.ResetPosition() end
	elseif msg == "refresh" then
		H.Refresh()
	elseif msg == "minimap" then
		H.ToggleMinimapButton()
	elseif msg:match("^sound") or msg:match("^toast") then
		local key, value = msg:match("^(%a+)%s*(%a*)$")
		if value == "on" or value == "off" then H.db.notify[key] = value == "on" end
		H.Print(key .. " notifications " .. (H.db.notify[key] and "on" or "off") .. ".")
	elseif msg == "help" then
		H.Print("/hermes - open or close the window")
		H.Print("/hermes ask <question> - ask without opening the window; the reply appears in chat")
		H.Print("/hermes look <question> - the same, with a screenshot of the game")
		H.Print("/hermes combat hide | fade | off - what the window does in combat")
		H.Print("/hermes status | refresh | reset | minimap | sound on/off | toast on/off")
	else
		if H.Toggle then H.Toggle() end
	end
end
