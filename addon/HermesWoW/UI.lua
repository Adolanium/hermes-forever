-- HermesWoW window: session list on the left, conversation on the right, built from the
-- client's own templates (ButtonFrameTemplate, InsetFrameTemplate, UIPanelScrollFrameTemplate,
-- UIPanelButtonTemplate) and stock textures and fonts; Markdown rendering; notifications.
-- (One file on purpose: the client only finds files that existed when it launched, so new
-- files would force a full restart instead of a /reload.)

local H = HermesWoW
local ui = {}
H.ui = ui

local LIST_WIDTH = 230
local ROW_HEIGHT = 38
local INPUT_HEIGHT = 74
local CHUNK = 3000          -- characters per FontString; longer messages continue in the next block

local ROLE = {
	user = { label = "You", color = { 0.55, 0.78, 1 } },
	assistant = { label = "Hermes", color = { 1, 0.82, 0 } },
	tool = { label = "Tools", color = { 0.6, 0.6, 0.6 } },
	system = { label = "Hermes", color = { 1, 0.5, 0.25 } },
	pending = { label = "You", color = { 0.55, 0.78, 1 } },
	error = { label = "Not sent", color = { 1, 0.3, 0.3 } },
}

local STATUS_ICON = {
	ok = "Interface\\FriendsFrame\\StatusIcon-Online",
	warn = "Interface\\FriendsFrame\\StatusIcon-Away",
	error = "Interface\\FriendsFrame\\StatusIcon-DnD",
	idle = "Interface\\FriendsFrame\\StatusIcon-Offline",
}

local CHOICE_LABEL = { once = "Allow once", session = "Allow this session", always = "Always allow", deny = "Deny" }

---------------------------------------------------------------------------
-- Markdown -> WoW text (tested by tests/format_test.py).
-- Hermes replies use Markdown. This renders the common parts with WoW escape codes:
-- headings (gold), **bold**, `code` and fenced blocks (blue-grey), bullets, quotes, rules,
-- and links. Links become |Hhermesurl:...|h hyperlinks the window turns into "copy" clicks.
-- Every "|" in the source text is escaped first, so content can never inject codes of its
-- own, and every line closes its own colors, so the result can be split at any newline.
---------------------------------------------------------------------------

local HEAD, BOLD, CODE, LINK, DIM = "|cffffd100", "|cffffe8a3", "|cffb4c8e6", "|cff4fa3ff", "|cff8c8c8c"
local LINK_TYPE = "hermesurl:"
H.LINK_TYPE = LINK_TYPE

local function Link(url, text)
	return "|H" .. LINK_TYPE .. url .. "|h" .. LINK .. text .. "|r|h"
end

-- Text outside code spans: links, bare URLs, **bold**.
local function Plain(s)
	local links = {}
	local function hold(v)
		links[#links + 1] = v
		return "\1" .. #links .. "\1"
	end
	s = s:gsub("%[([^%]\1]-)%]%((https?://[^%s%)|]+)%)", function(text, url) return hold(Link(url, text)) end)
	s = s:gsub("https?://[^%s<>\"'%)%]|\1]+", function(url)
		local trail = url:match("[%.,;:!%?]+$") or ""
		url = url:sub(1, #url - #trail)
		return hold(Link(url, url)) .. trail
	end)
	s = s:gsub("%*%*([^\1]-)%*%*", BOLD .. "%1|r")
	return (s:gsub("\1(%d+)\1", function(k) return links[tonumber(k)] end))
end

-- One line: `code` spans verbatim, the rest through Plain. An unmatched backtick stays literal.
local function Inline(s)
	local parts = {}
	for p in (s .. "`"):gmatch("([^`]*)`") do parts[#parts + 1] = p end
	if #parts % 2 == 0 then
		parts[#parts - 1] = parts[#parts - 1] .. "`" .. parts[#parts]
		parts[#parts] = nil
	end
	for i, p in ipairs(parts) do
		if i % 2 == 0 then
			parts[i] = p == "" and "``" or (CODE .. p .. "|r")
		else
			parts[i] = Plain(p)
		end
	end
	return table.concat(parts)
end

-- Markers removed, for lines drawn in a single color (headings, quotes).
local function Bare(s)
	return (s:gsub("%*%*", ""):gsub("`", ""))
end

function H.Format(text)
	text = (text or ""):gsub("|", "||")
	local out, fence = {}, false
	for line in (text .. "\n"):gmatch("([^\n]*)\n") do
		if line:match("^%s*```") then
			fence = not fence
		elseif fence then
			out[#out + 1] = line == "" and " " or (CODE .. line .. "|r")
		else
			local hashes, head = line:match("^%s*(#+)%s+(.*)$")
			local indent, bullet = line:match("^(%s*)[%-%*%+]%s+(.*)$")
			local quote = line:match("^%s*>%s?(.*)$")
			if hashes and #hashes <= 6 then
				out[#out + 1] = HEAD .. Bare(head) .. "|r"
			elseif line:match("^%s*[%-%*_]%s*[%-%*_]%s*[%-%*_][%s%-%*_]*$") then
				out[#out + 1] = DIM .. string.rep("-", 32) .. "|r"
			elseif bullet then
				out[#out + 1] = string.rep("    ", math.floor(#indent / 2)) .. "\226\128\162 " .. Inline(bullet)
			elseif quote then
				out[#out + 1] = DIM .. "> " .. Bare(quote) .. "|r"
			else
				out[#out + 1] = Inline(line)
			end
		end
	end
	return table.concat(out, "\n")
end

local function FmtDuration(s)
	s = math.max(0, math.floor(s or 0))
	if s < 60 then return s .. "s" end
	if s < 3600 then return string.format("%d:%02d", math.floor(s / 60), s % 60) end
	return string.format("%dh %02dm", math.floor(s / 3600), math.floor(s / 60) % 60)
end

local function FmtAge(ts)
	if not ts or ts == 0 then return "" end
	local d = time() - ts
	if d < 3600 then return math.max(1, math.floor(d / 60)) .. "m ago" end
	if d < 86400 then return math.floor(d / 3600) .. "h ago" end
	return math.floor(d / 86400) .. "d ago"
end

local function Button(parent, text, width, onClick)
	local b = CreateFrame("Button", nil, parent, "UIPanelButtonTemplate")
	b:SetSize(width, 22)
	b:SetText(text)
	b:SetScript("OnClick", onClick)
	return b
end

local function Tooltip(frame, fn)
	frame:SetScript("OnEnter", function(self)
		GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
		fn(GameTooltip, self)
		GameTooltip:Show()
	end)
	frame:SetScript("OnLeave", GameTooltip_Hide)
end

---------------------------------------------------------------------------
-- Copy dialog: FontStrings cannot be selected, so a click opens the text in an EditBox.
---------------------------------------------------------------------------

local function CopyDialog()
	if ui.copy then return ui.copy end
	local d = CreateFrame("Frame", "HermesWoWCopyFrame", UIParent, "BackdropTemplate")
	d:SetSize(560, 360)
	d:SetPoint("CENTER")
	d:SetFrameStrata("DIALOG")
	d:SetToplevel(true)
	d:EnableMouse(true)
	d:SetBackdrop({
		bgFile = "Interface\\DialogFrame\\UI-DialogBox-Background",
		edgeFile = "Interface\\DialogFrame\\UI-DialogBox-Border",
		tile = true, tileSize = 32, edgeSize = 32,
		insets = { left = 11, right = 12, top = 12, bottom = 11 },
	})
	local title = d:CreateFontString(nil, "ARTWORK", "GameFontNormal")
	title:SetPoint("TOP", 0, -18)
	title:SetText("Select the text and press Ctrl+C to copy")
	local close = CreateFrame("Button", nil, d, "UIPanelCloseButton")
	close:SetPoint("TOPRIGHT", -6, -6)
	local sf = CreateFrame("ScrollFrame", "HermesWoWCopyScroll", d, "UIPanelScrollFrameTemplate")
	sf:SetPoint("TOPLEFT", 20, -42)
	sf:SetPoint("BOTTOMRIGHT", -40, 20)
	local eb = CreateFrame("EditBox", nil, sf)
	eb:SetMultiLine(true)
	eb:SetAutoFocus(false)
	eb:SetFontObject(ChatFontNormal)
	eb:SetWidth(490)
	eb:SetScript("OnEscapePressed", function() d:Hide() end)
	eb:SetScript("OnTextChanged", function(self, user)
		if user then self:SetText(d.text or "") self:HighlightText() end   -- read-only
	end)
	sf:SetScrollChild(eb)
	d.edit = eb
	d:Hide()
	tinsert(UISpecialFrames, "HermesWoWCopyFrame")
	ui.copy = d
	return d
end

local function ShowCopy(text)
	local d = CopyDialog()
	d.text = text or ""
	d:Show()
	d.edit:SetText(d.text)
	d.edit:SetFocus()
	d.edit:HighlightText()
end

---------------------------------------------------------------------------
-- Session list
---------------------------------------------------------------------------

local function CreateRow(parent)
	local b = CreateFrame("Button", nil, parent)
	b:SetHeight(ROW_HEIGHT)
	b:SetHighlightTexture("Interface\\QuestFrame\\UI-QuestTitleHighlight", "ADD")
	b.sel = b:CreateTexture(nil, "BACKGROUND")
	b.sel:SetAllPoints()
	b.sel:SetTexture("Interface\\QuestFrame\\UI-QuestLogTitleHighlight")
	b.sel:SetBlendMode("ADD")
	b.sel:SetVertexColor(0.82, 0.62, 0.1, 0.55)
	b.dot = b:CreateTexture(nil, "ARTWORK")
	b.dot:SetSize(14, 14)
	b.dot:SetPoint("TOPLEFT", 4, -4)
	b.title = b:CreateFontString(nil, "ARTWORK", "GameFontNormal")
	b.title:SetPoint("TOPLEFT", 20, -5)
	b.title:SetPoint("RIGHT", -34, 0)
	b.title:SetJustifyH("LEFT")
	b.title:SetWordWrap(false)
	b.sub = b:CreateFontString(nil, "ARTWORK", "GameFontDisableSmall")
	b.sub:SetPoint("TOPLEFT", b.title, "BOTTOMLEFT", 0, -3)
	b.sub:SetPoint("RIGHT", -6, 0)
	b.sub:SetJustifyH("LEFT")
	b.sub:SetWordWrap(false)
	b.badge = b:CreateFontString(nil, "ARTWORK", "GameFontGreenSmall")
	b.badge:SetPoint("TOPRIGHT", -6, -6)
	b:SetScript("OnClick", function(self)
		if ui.input then H.SaveDraft() end
		H.Open(self.sid)
	end)
	Tooltip(b, function(tt, self)
		local s = self.row
		tt:AddLine(H.Escape(s.title ~= "" and s.title or "Untitled"), 1, 1, 1, true)
		tt:AddLine("Status: " .. s.status, nil, nil, nil)
		tt:AddLine(s.n .. " messages" .. (s.at and s.at > 0 and (", started " .. date("%Y-%m-%d %H:%M", s.at)) or ""))
		if s.model and s.model ~= "" then tt:AddLine("Model: " .. H.Escape(s.model)) end
		if s.src and s.src ~= "" then tt:AddLine("Source: " .. H.Escape(s.src)) end
		tt:AddLine(H.Escape(s.id), 0.5, 0.5, 0.5)
	end)
	return b
end

local function RenderList()
	local content = ui.listContent
	local sessions = H.Sessions()
	ui.rows = ui.rows or {}
	local width = ui.listScroll:GetWidth()
	content:SetWidth(width)
	for i, s in ipairs(sessions) do
		local b = ui.rows[i]
		if not b then
			b = CreateRow(content)
			ui.rows[i] = b
		end
		b:ClearAllPoints()
		b:SetPoint("TOPLEFT", 0, -(i - 1) * ROW_HEIGHT)
		b:SetWidth(width)
		b.sid, b.row = s.id, s
		b.title:SetText(H.Escape(s.title ~= "" and s.title or "Untitled"))
		local selected = s.id == H.db.selected
		local unread = H.IsUnread(s.id) and not selected
		if selected then b.sel:Show() else b.sel:Hide() end
		b.title:SetFontObject((selected or unread) and "GameFontHighlight" or "GameFontNormal")
		local status = s.status
		local icon = (status == "running" and STATUS_ICON.ok) or (status == "waiting" and STATUS_ICON.warn) or STATUS_ICON.idle
		b.dot:SetTexture(icon)
		local label = status == "running" and "|cff20ff20working|r" or (status == "waiting" and "|cffffd100needs you|r") or nil
		b.sub:SetText((label and (label .. "  ") or "") .. s.n .. " msgs  " .. FmtAge(s.at))
		b.badge:SetText(unread and "NEW" or "")
		b:Show()
	end
	for i = #sessions + 1, #ui.rows do ui.rows[i]:Hide() end
	content:SetHeight(math.max(1, #sessions * ROW_HEIGHT))
	ui.listEmpty:SetShown(#sessions == 0)
end

---------------------------------------------------------------------------
-- Conversation
---------------------------------------------------------------------------

-- A click on a link inside the block also clicks the block: keep the link's copy box.
local function BlockClick(self)
	if self.linkAt and GetTime() - self.linkAt < 0.5 then return end
	if self.onClick then self.onClick(self) elseif self.copyText then ShowCopy(self.copyText) end
end

local function Block(i)
	ui.blocks = ui.blocks or {}
	local b = ui.blocks[i]
	if b then return b end
	b = CreateFrame("Button", nil, ui.convContent)
	b.head = b:CreateFontString(nil, "ARTWORK", "GameFontNormalSmall")
	b.head:SetPoint("TOPLEFT", 4, 0)
	b.head:SetJustifyH("LEFT")
	b.body = b:CreateFontString(nil, "ARTWORK", "ChatFontNormal")
	b.body:SetJustifyH("LEFT")
	b.body:SetJustifyV("TOP")
	b.body:SetSpacing(2)
	b:SetScript("OnClick", BlockClick)
	if b.SetHyperlinksEnabled then b:SetHyperlinksEnabled(true) end
	b:SetScript("OnHyperlinkClick", function(self, link)
		if link:sub(1, #H.LINK_TYPE) == H.LINK_TYPE then
			self.linkAt = GetTime()
			ShowCopy(link:sub(#H.LINK_TYPE + 1))
		end
	end)
	b:SetScript("OnHyperlinkEnter", function(self, link)
		if link:sub(1, #H.LINK_TYPE) ~= H.LINK_TYPE then return end
		GameTooltip:SetOwner(self, "ANCHOR_CURSOR")
		GameTooltip:AddLine("Click to copy this link", 0.8, 0.8, 0.8)
		GameTooltip:AddLine(H.Escape(link:sub(#H.LINK_TYPE + 1)), 0.31, 0.64, 1, true)
		GameTooltip:Show()
	end)
	b:SetScript("OnHyperlinkLeave", GameTooltip_Hide)
	b:SetScript("OnEnter", function(self)
		if self.copyText then
			GameTooltip:SetOwner(self, "ANCHOR_CURSOR")
			GameTooltip:AddLine(self.hint or "Click to copy this message", 0.8, 0.8, 0.8)
			GameTooltip:Show()
		end
	end)
	b:SetScript("OnLeave", GameTooltip_Hide)
	ui.blocks[i] = b
	return b
end

-- Lay out a list of { role, head, text, color, copy } entries top to bottom.
local function Layout(entries)
	local width = ui.convScroll:GetWidth() - 8
	ui.convContent:SetWidth(width + 8)
	local y = -6
	local n = 0
	for _, e in ipairs(entries) do
		-- Rich entries are Markdown (formatted per line, colors closed per line), the rest plain
		-- text; both come out with every "|" escaped. Long text is split at line breaks only,
		-- so no split lands inside an escape code.
		local text = e.rich and H.Format(e.text) or H.Escape(e.text or "")
		local chunks = {}
		local cur, size = {}, 0
		for line in (text .. "\n"):gmatch("([^\n]*)\n") do
			if size > 0 and size + #line > CHUNK then
				chunks[#chunks + 1] = table.concat(cur, "\n")
				cur, size = {}, 0
			end
			cur[#cur + 1] = line
			size = size + #line + 1
		end
		chunks[#chunks + 1] = table.concat(cur, "\n")
		for ci, chunk in ipairs(chunks) do
			n = n + 1
			local b = Block(n)
			b:ClearAllPoints()
			b:SetPoint("TOPLEFT", ui.convContent, "TOPLEFT", 0, y)
			b:SetWidth(width)
			local headH = 0
			if ci == 1 and e.head then
				b.head:SetText(e.head)
				b.head:Show()
				headH = 14
			else
				b.head:SetText("")
				b.head:Hide()
			end
			b.body:ClearAllPoints()
			b.body:SetPoint("TOPLEFT", 4, -headH)
			b.body:SetWidth(width - 8)
			b.body:SetFontObject(_G[e.font or "ChatFontNormal"] or ChatFontNormal)
			local c = e.color or { 1, 1, 1 }
			b.body:SetTextColor(c[1], c[2], c[3])
			b.body:SetText(chunk)
			local h = headH + b.body:GetStringHeight() + 2
			b:SetHeight(h)
			b.copyText = e.copy
			b.hint = e.hint
			b.onClick = e.onClick
			b:Show()
			y = y - h - ((ci == #chunks) and 10 or 0)
		end
	end
	for i = n + 1, #(ui.blocks or {}) do ui.blocks[i]:Hide() end
	ui.convContent:SetHeight(math.max(1, -y))
end

local function RoleHead(role, ts, extra)
	local r = ROLE[role] or ROLE.system
	local c = r.color
	local s = string.format("|cff%02x%02x%02x%s|r", math.floor(c[1] * 255), math.floor(c[2] * 255), math.floor(c[3] * 255), r.label)
	if ts then s = s .. "  |cff808080" .. date("%H:%M", ts) .. "|r" end
	if extra then s = s .. "  " .. extra end
	return s
end

local function ConversationEntries(sid)
	local entries = {}
	local v, cached = H.View(sid)
	if not sid then
		entries[#entries + 1] = { text = "New conversation.\n\nType a message below and press Enter. Hermes starts a new session with your profile's model, tools and approval settings.", color = { 0.8, 0.8, 0.8 }, font = "GameFontHighlight" }
	elseif not v then
		entries[#entries + 1] = { text = "Loading this session from Hermes...", color = { 0.7, 0.7, 0.7 }, font = "GameFontHighlight" }
	else
		if v.total and v.shown and v.total > v.shown then
			entries[#entries + 1] = {
				text = string.format("%d earlier messages not shown. Click to load more.", v.total - v.shown),
				color = { 0.4, 0.75, 1 }, font = "GameFontNormalSmall",
				copy = "", hint = "Load older messages",
				onClick = function() H.LoadOlder(sid) end,
			}
		end
		for _, m in ipairs(v.msgs or {}) do
			if m.r == "tool" then
				entries[#entries + 1] = { text = m.t, color = { 0.6, 0.6, 0.6 }, font = "GameFontDisableSmall", copy = m.t }
			else
				entries[#entries + 1] = { head = RoleHead(m.r, m.ts), text = m.t, copy = m.t, rich = m.r == "assistant",
					color = m.r == "user" and { 0.9, 0.95, 1 } or { 1, 1, 1 } }
			end
		end
		if #(v.msgs or {}) == 0 and v.loaded then
			entries[#entries + 1] = { text = "No messages yet.", color = { 0.7, 0.7, 0.7 }, font = "GameFontHighlight" }
		end
		for _, p in ipairs(v.pending or {}) do
			if p.status == "queued" then
				entries[#entries + 1] = { head = RoleHead("pending", nil, "|cffffd100queued - Hermes will run it after the current turn|r"),
					text = p.text, color = { 0.75, 0.75, 0.75 }, copy = p.text }
			end
		end
		if v.running and v.live then
			local live = v.live
			local lines = {}
			for _, l in ipairs(live.lines or {}) do lines[#lines + 1] = l end
			local head = RoleHead("assistant", nil, "|cff20ff20working " .. FmtDuration(time() - (live.since or time()))
				.. ((live.tools or 0) > 0 and (" - " .. live.tools .. " tool calls") or "") .. "|r")
			entries[#entries + 1] = { head = head, text = table.concat(lines, "\n"), color = { 0.6, 0.6, 0.6 }, font = "GameFontDisableSmall" }
			if live.text and live.text ~= "" then
				entries[#entries + 1] = { text = live.text, color = { 0.9, 0.9, 0.9 }, copy = live.text, rich = true }
			end
		end
		if v.err then
			entries[#entries + 1] = { head = RoleHead("system"), text = v.err, color = { 1, 0.45, 0.35 }, copy = v.err }
		end
		if cached then
			entries[#entries + 1] = { text = "(Saved copy. Waiting for the companion to send the current state.)", color = { 0.6, 0.6, 0.6 }, font = "GameFontDisableSmall" }
		end
	end
	for _, p in ipairs(H.PendingSends(sid)) do
		local s = p.s
		local state = s.state or "sending"
		local note = ({
			sending = "sending to the companion...",
			receiving = "sending to the companion...",
			received = "handing to Hermes...",
			created = "handing to Hermes...",
			waiting = "waiting for the Hermes gateway",
			retrying = "connection problem, retrying...",
			error = "not sent: " .. (s.err or "unknown error"),
		})[state] or state
		local isErr = state == "error"
		entries[#entries + 1] = {
			head = RoleHead(isErr and "error" or "pending", s.t, "|cffa0a0a0" .. H.Escape(note) .. "|r"),
			text = s.text, color = isErr and { 1, 0.6, 0.6 } or { 0.75, 0.75, 0.75 }, copy = s.text,
			hint = isErr and "Click to put this text back in the message box" or nil,
			onClick = isErr and function()
				ui.input:SetText(s.text)
				H.Dismiss(p.mid)
			end or nil,
		}
	end
	return entries
end

local function AtBottom()
	local range = ui.convScroll:GetVerticalScrollRange()
	return range <= 0 or ui.convScroll:GetVerticalScroll() >= range - 20
end

local function RenderConversation(force)
	local sid = H.db.selected
	local v = H.View(sid)
	local sig = table.concat({ tostring(sid), tostring(H.run.inbox and H.run.inbox.rev), tostring(H.db.nextId),
		tostring(ui.convScroll:GetWidth()), tostring(v and v.running and v.live and math.floor((time() - (v.live.since or 0)) / 5)) }, "|")
	if sig == ui.convSig and not force then return end
	local stick = AtBottom() or ui.convSid ~= sid
	ui.convSig, ui.convSid = sig, sid
	Layout(ConversationEntries(sid))
	if stick then
		C_Timer.After(0, function()
			ui.convScroll:UpdateScrollChildRect()
			ui.convScroll:SetVerticalScroll(ui.convScroll:GetVerticalScrollRange())
		end)
	end
end

---------------------------------------------------------------------------
-- Approval / clarify panel
---------------------------------------------------------------------------

local function RenderPrompt()
	local p = ui.prompt
	local sid = H.db.selected
	local v = sid and H.View(sid)
	local prompt = v and v.prompts and v.prompts[1]
	p.choiceButtons = p.choiceButtons or {}
	for _, b in ipairs(p.choiceButtons) do b:Hide() end
	if not prompt then
		p:Hide()
		ui.convInset:SetPoint("BOTTOMRIGHT", ui.frame, "BOTTOMRIGHT", -8, INPUT_HEIGHT + 38)
		return
	end
	local more = #v.prompts > 1 and string.format("  |cff808080(+%d more)|r", #v.prompts - 1) or ""
	if prompt.kind == "approval" then
		p.title:SetText("Hermes asks for approval" .. more)
		local cmd, desc = prompt.cmd or "", prompt.desc or ""
		local body = cmd ~= "" and cmd or desc
		if cmd ~= "" and desc ~= "" then body = desc .. "\n" .. cmd end
		p.text:SetText(H.Escape(body))
	else
		p.title:SetText("Hermes has a question" .. more)
		p.text:SetText(H.Escape(prompt.q) .. "\n|cff808080Pick an answer, or type one below and click Answer.|r")
	end
	local choices = {}
	for _, c in ipairs(prompt.choices or {}) do choices[#choices + 1] = c end
	if prompt.kind == "clarify" then choices[#choices + 1] = "\0typed" end
	local x = 0
	for i, c in ipairs(choices) do
		local b = p.choiceButtons[i]
		if not b then
			b = Button(p, "", 100, nil)
			p.choiceButtons[i] = b
		end
		local label = c == "\0typed" and "Answer" or (prompt.kind == "approval" and CHOICE_LABEL[c] or c)
		b:SetText(H.Escape(label))
		b:SetWidth(math.min(math.max(b:GetFontString():GetStringWidth() + 24, 70), 220))
		b:ClearAllPoints()
		b:SetPoint("BOTTOMLEFT", p, "BOTTOMLEFT", 10 + x, 8)
		x = x + b:GetWidth() + 4
		b:SetScript("OnClick", function()
			if prompt.kind == "approval" then
				H.Approve(sid, prompt.id, c)
			elseif c == "\0typed" then
				local text = H.InputText()
				if text == "" then H.Print("Type your answer in the message box first.") return end
				H.Answer(sid, prompt.id, text)
				ui.input:SetText("")
			else
				H.Answer(sid, prompt.id, c)
			end
			p.sentFor = prompt.id
			RenderPrompt()
		end)
		b:SetEnabled(p.sentFor ~= prompt.id)
		b:Show()
	end
	local h = 44 + math.min(p.text:GetStringHeight(), 90) + 30
	p:SetHeight(h)
	p:Show()
	ui.convInset:SetPoint("BOTTOMRIGHT", ui.frame, "BOTTOMRIGHT", -8, INPUT_HEIGHT + 38 + h + 4)
end

---------------------------------------------------------------------------
-- Header, status, buttons
---------------------------------------------------------------------------

local function RenderHeader()
	local sid = H.db.selected
	local v = sid and H.View(sid)
	local row = sid and H.SessionRow(sid)
	local title = (v and v.title ~= "" and v.title) or (row and row.title ~= "" and row.title) or (sid and "Untitled session") or "New conversation"
	ui.title:SetText(H.Escape(title))
	local sub
	if not sid then
		sub = "Starts a new Hermes session"
	elseif v and v.prompts and #v.prompts > 0 then
		sub = "|cffffd100Waiting for you|r"
	elseif v and v.running then
		sub = "|cff20ff20Working|r " .. FmtDuration(time() - ((v.live and v.live.since) or time()))
	elseif H.Busy(sid) then
		sub = "|cffffd100Sending...|r"
	else
		sub = "Idle"
	end
	local model = (v and v.model) or (row and row.model)
	if model and model ~= "" then sub = sub .. "  |cff808080" .. H.Escape(model) .. "|r" end
	ui.subtitle:SetText(sub)
	ui.stop:SetEnabled(v and v.running and true or false)
	ui.send:SetText(sid and "Send" or "Start")
	ui.modelBtn:SetShown(not sid)
	if sid then
		if ui.picker then ui.picker:Hide() end
	else
		ui.modelBtn:SetText("Model: " .. H.Escape(H.ModelLabel()))
	end
end

local function RenderStatus()
	local level, text = H.Status()
	ui.statusDot:SetTexture(STATUS_ICON[level])
	local extra = {}
	local pend = H.PendingRecords()
	if pend > 0 then extra[#extra + 1] = "|cffffd100" .. pend .. " pending|r" end
	local unread = 0
	for _, s in ipairs(H.Sessions()) do
		if s.id ~= H.db.selected and H.IsUnread(s.id) then unread = unread + 1 end
	end
	if unread > 0 then extra[#extra + 1] = "|cff20ff20" .. unread .. " unread|r" end
	local color = level == "ok" and "|cffffffff" or (level == "warn" and "|cffffd100" or "|cffff4040")
	ui.statusText:SetText(color .. text .. "|r" .. (#extra > 0 and ("   " .. table.concat(extra, "   ")) or ""))
	local left = H.SlotsLeft()
	ui.slots:SetText(string.format("|cff%s%d reply slots|r", left < 25 and "ff4040" or "808080", left))
	ui.reload:SetShown(left < 25 or H.run.slotsExhausted)
end

function H.Render()
	if not ui.frame or not ui.frame:IsShown() then return end
	RenderList()
	RenderPrompt()
	RenderConversation()
	RenderHeader()
	RenderStatus()
end

function H.OnChanged()
	if not ui.frame then return end
	if ui.frame:IsShown() and H.db.selected then H.MarkSeen(H.db.selected) end
	H.Render()
end

local tickN = 0
function H.OnTick()
	if not ui.frame or not ui.frame:IsShown() then return end
	tickN = tickN + 1
	if tickN % 4 == 0 then
		RenderHeader()
		RenderStatus()
		RenderConversation()
	end
end

function H.IsShown() return ui.frame and ui.frame:IsShown() or false end

---------------------------------------------------------------------------
-- Input
---------------------------------------------------------------------------

-- What the player typed: links reduced to "[Name]", the edit box's "||" back to "|".
function H.InputText()
	return (H.ExpandLinks(ui.input:GetText()))
end

function H.SaveDraft()
	H.db.drafts[H.db.selected or "new"] = ui.input:GetText()
end

local function LoadDraft()
	ui.input:SetText(H.db.drafts[H.db.selected or "new"] or "")
end

local function DoSend()
	local sid = H.db.selected
	local text, links = H.ExpandLinks(ui.input:GetText())
	local mid, why = H.Send(sid, text, H.ModelExtras(links), not sid and H.db.newModel or nil)
	if not mid then
		if why == "too long" then
			H.Print(string.format("Message too long (limit %d bytes, including linked tooltips and game info).", H.MAX_MESSAGE))
		end
		return
	end
	ui.input:SetText("")
	H.db.drafts[sid or "new"] = nil
	ui.input:ClearFocus()      -- give the keyboard back to the game
end

local function CreateInput(f)
	local bg = CreateFrame("Frame", nil, f, "BackdropTemplate")
	bg:SetBackdrop({
		bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
		edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
		tile = true, tileSize = 16, edgeSize = 14,
		insets = { left = 3, right = 3, top = 3, bottom = 3 },
	})
	bg:SetBackdropColor(0, 0, 0, 0.75)
	bg:SetBackdropBorderColor(0.55, 0.55, 0.55)
	bg:SetPoint("BOTTOMLEFT", f, "BOTTOMLEFT", LIST_WIDTH + 16, 34)
	bg:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -100, 34)
	bg:SetHeight(INPUT_HEIGHT)
	local sf = CreateFrame("ScrollFrame", "HermesWoWInputScroll", bg, "UIPanelScrollFrameTemplate")
	sf:SetPoint("TOPLEFT", 8, -6)
	sf:SetPoint("BOTTOMRIGHT", -28, 6)
	local eb = CreateFrame("EditBox", "HermesWoWInput", sf)
	eb:SetMultiLine(true)
	eb:SetAutoFocus(false)
	eb:SetFontObject(ChatFontNormal)
	eb:SetTextInsets(0, 0, 2, 2)
	if eb.SetMaxBytes then eb:SetMaxBytes(H.MAX_MESSAGE + 1) end
	eb:SetWidth(300)
	sf:SetScrollChild(eb)
	sf:SetScript("OnSizeChanged", function(self, w) eb:SetWidth(w) end)
	bg:EnableMouse(true)
	bg:SetScript("OnMouseDown", function() eb:SetFocus() end)
	sf:EnableMouse(true)
	sf:SetScript("OnMouseDown", function() eb:SetFocus() end)
	local hint = bg:CreateFontString(nil, "ARTWORK", "GameFontDisable")
	hint:SetPoint("TOPLEFT", 10, -9)
	hint:SetText("Message Hermes...  (Enter sends, Shift+Enter adds a line, Shift-click items to link them)")
	local count = bg:CreateFontString(nil, "ARTWORK", "GameFontDisableSmall")
	count:SetPoint("BOTTOMRIGHT", bg, "TOPRIGHT", -4, 2)
	local function UpdateHint()
		hint:SetShown(eb:GetText() == "" and not eb:HasFocus())
		local n = #eb:GetText()
		count:SetText(n > 2000 and string.format("%d / %d", n, H.MAX_MESSAGE) or "")
	end
	eb:SetScript("OnEscapePressed", function(self) self:ClearFocus() end)
	eb:SetScript("OnEnterPressed", function(self)
		if IsShiftKeyDown() then self:Insert("\n") else DoSend() end
	end)
	eb:SetScript("OnTextChanged", function(self)
		UpdateHint()
		if ScrollingEdit_OnTextChanged then ScrollingEdit_OnTextChanged(self, sf) end
	end)
	eb:SetScript("OnCursorChanged", function(self, x, y, w, h)
		if ScrollingEdit_OnCursorChanged then ScrollingEdit_OnCursorChanged(self, x, y, w, h) end
	end)
	eb:SetScript("OnUpdate", function(self, elapsed)
		if ScrollingEdit_OnUpdate then ScrollingEdit_OnUpdate(self, elapsed, sf) end
	end)
	eb:SetScript("OnEditFocusGained", UpdateHint)
	eb:SetScript("OnEditFocusLost", function() UpdateHint() H.SaveDraft() end)
	ui.input, ui.inputBg = eb, bg
	UpdateHint()
end

---------------------------------------------------------------------------
-- Window
---------------------------------------------------------------------------

---------------------------------------------------------------------------
-- Model picker for new conversations
---------------------------------------------------------------------------

local PICK_ROW = 18

function H.ModelLabel()
	local m = H.db.newModel
	if m and m.m then return m.m end
	local models = H.Models()
	return "Default" .. ((models and models.cur) and (" (" .. models.cur .. ")") or "")
end

local function ModelPicker()
	if ui.picker then return ui.picker end
	local p = CreateFrame("Frame", "HermesWoWModelPicker", ui.frame, "BackdropTemplate")
	p:SetSize(330, 360)
	p:SetPoint("TOPRIGHT", ui.modelBtn, "BOTTOMRIGHT", 0, -2)
	p:SetFrameStrata("DIALOG")
	p:SetToplevel(true)
	p:EnableMouse(true)
	p:SetBackdrop({
		bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
		edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
		tile = true, tileSize = 16, edgeSize = 16,
		insets = { left = 4, right = 4, top = 4, bottom = 4 },
	})
	p:SetBackdropColor(0, 0, 0, 0.95)
	local sf = CreateFrame("ScrollFrame", "HermesWoWModelScroll", p, "UIPanelScrollFrameTemplate")
	sf:SetPoint("TOPLEFT", 8, -8)
	sf:SetPoint("BOTTOMRIGHT", -30, 8)
	p.content = CreateFrame("Frame", nil, sf)
	p.content:SetSize(290, 1)
	sf:SetScrollChild(p.content)
	p.rows = {}
	p:Hide()
	tinsert(UISpecialFrames, "HermesWoWModelPicker")
	ui.picker = p
	return p
end

local function PickerRow(p, i)
	if p.rows[i] then return p.rows[i] end
	local b = CreateFrame("Button", nil, p.content)
	b:SetHeight(PICK_ROW)
	b:SetHighlightTexture("Interface\\QuestFrame\\UI-QuestTitleHighlight", "ADD")
	b.text = b:CreateFontString(nil, "ARTWORK", "GameFontHighlightSmall")
	b.text:SetPoint("LEFT", 4, 0)
	b.text:SetPoint("RIGHT", -20, 0)
	b.text:SetJustifyH("LEFT")
	b.text:SetWordWrap(false)
	b.check = b:CreateTexture(nil, "OVERLAY")
	b.check:SetSize(16, 16)
	b.check:SetPoint("RIGHT", -2, 0)
	b.check:SetTexture("Interface\\Buttons\\UI-CheckBox-Check")
	p.rows[i] = b
	return b
end

local function FillPicker()
	local p = ModelPicker()
	local models = H.Models()
	local entries = { { label = "Default (your profile's model)", pick = false } }
	if models and models.list then
		for _, prov in ipairs(models.list) do
			entries[#entries + 1] = { header = prov.n or prov.p }
			for _, m in ipairs(prov.m or {}) do entries[#entries + 1] = { label = m, pick = { p = prov.p, m = m } } end
		end
	else
		entries[#entries + 1] = { header = "The model list has not arrived from the companion yet." }
	end
	local cur = H.db.newModel
	for i, e in ipairs(entries) do
		local b = PickerRow(p, i)
		b:ClearAllPoints()
		b:SetPoint("TOPLEFT", 0, -(i - 1) * PICK_ROW)
		b:SetWidth(290)
		if e.header then
			b.text:SetFontObject(GameFontNormalSmall)
			b.text:SetText(H.Escape(e.header))
			b:EnableMouse(false)
			b.check:Hide()
		else
			b.text:SetFontObject(GameFontHighlightSmall)
			b.text:SetText("    " .. H.Escape(e.label))
			b:EnableMouse(true)
			local on = (e.pick == false and not cur) or (e.pick and cur and cur.p == e.pick.p and cur.m == e.pick.m)
			b.check:SetShown(on and true or false)
			b:SetScript("OnClick", function()
				H.db.newModel = e.pick or nil
				p:Hide()
				H.Changed()
			end)
		end
		b:Show()
	end
	for i = #entries + 1, #p.rows do p.rows[i]:Hide() end
	p.content:SetHeight(#entries * PICK_ROW)
end

local function SavePosition()
	local f = ui.frame
	local point, _, rel, x, y = f:GetPoint(1)
	H.db.pos = { point, rel, x, y }
	H.db.size = { f:GetWidth(), f:GetHeight() }
end

local function ApplyPosition()
	local f = ui.frame
	f:ClearAllPoints()
	local p = H.db.pos
	if p then f:SetPoint(p[1], UIParent, p[2], p[3], p[4]) else f:SetPoint("CENTER") end
	local s = H.db.size
	f:SetSize(s and s[1] or 860, s and s[2] or 560)
end

function H.ResetPosition()
	H.db.pos, H.db.size = nil, nil
	if ui.frame then ApplyPosition() end
end

function H.Toggle()
	if not ui.frame then return end
	if ui.frame:IsShown() then ui.frame:Hide() else ui.frame:Show() end
end

function H.CreateUI()
	if ui.frame then return end
	local ok, f = pcall(CreateFrame, "Frame", "HermesWoWFrame", UIParent, "ButtonFrameTemplate")
	if not ok then f = CreateFrame("Frame", "HermesWoWFrame", UIParent, "BasicFrameTemplateWithInset") end
	ui.frame = f
	if ButtonFrameTemplate_HidePortrait and (f.PortraitContainer or f.portrait) then pcall(ButtonFrameTemplate_HidePortrait, f) end
	if ButtonFrameTemplate_HideButtonBar then pcall(ButtonFrameTemplate_HideButtonBar, f) end
	if f.SetTitle then f:SetTitle("Hermes") elseif f.TitleText then f.TitleText:SetText("Hermes") end
	f:SetFrameStrata("MEDIUM")
	f:SetToplevel(true)
	f:SetClampedToScreen(true)
	f:SetMovable(true)
	f:SetResizable(true)
	if f.SetResizeBounds then f:SetResizeBounds(660, 420, 1600, 1200) elseif f.SetMinResize then f:SetMinResize(660, 420) end
	f:EnableMouse(true)
	f:RegisterForDrag("LeftButton")
	f:SetScript("OnDragStart", f.StartMoving)
	f:SetScript("OnDragStop", function(self) self:StopMovingOrSizing() SavePosition() end)
	f:SetScript("OnShow", function()
		if H.db.selected then H.MarkSeen(H.db.selected) end
		H.Render()
	end)
	f:SetScript("OnHide", function() if ui.input then ui.input:ClearFocus() H.SaveDraft() end end)
	f:SetScript("OnSizeChanged", function() if f:IsShown() then H.Render() end end)
	tinsert(UISpecialFrames, "HermesWoWFrame")
	ApplyPosition()

	-- Resize grip, the chat frame's own textures.
	local grip = CreateFrame("Button", nil, f)
	grip:SetSize(16, 16)
	grip:SetPoint("BOTTOMRIGHT", -4, 4)
	grip:SetFrameLevel(f:GetFrameLevel() + 10)
	grip:SetNormalTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Up")
	grip:SetHighlightTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Highlight")
	grip:SetPushedTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Down")
	grip:SetScript("OnMouseDown", function() f:StartSizing("BOTTOMRIGHT") end)
	grip:SetScript("OnMouseUp", function() f:StopMovingOrSizing() SavePosition() H.Render() end)

	-- Toolbar
	ui.new = Button(f, "New", 70, function()
		H.SaveDraft()
		H.db.selected = nil
		LoadDraft()
		H.Changed()
		ui.input:SetFocus()
	end)
	ui.new:SetPoint("TOPLEFT", f, "TOPLEFT", 10, -30)
	Tooltip(ui.new, function(tt) tt:AddLine("New conversation", 1, 1, 1) tt:AddLine("Your first message starts a new Hermes session.", nil, nil, nil, true) end)
	ui.refresh = Button(f, "Refresh", 80, function() H.Refresh() end)
	ui.refresh:SetPoint("LEFT", ui.new, "RIGHT", 4, 0)
	Tooltip(ui.refresh, function(tt) tt:AddLine("Refresh sessions", 1, 1, 1) tt:AddLine("Asks the companion for the session list and reloads the open conversation.", nil, nil, nil, true) end)

	ui.title = f:CreateFontString(nil, "ARTWORK", "GameFontHighlightLarge")
	ui.title:SetPoint("TOPLEFT", f, "TOPLEFT", LIST_WIDTH + 18, -30)
	ui.title:SetPoint("RIGHT", f, "RIGHT", -100, 0)
	ui.title:SetJustifyH("LEFT")
	ui.title:SetWordWrap(false)
	ui.subtitle = f:CreateFontString(nil, "ARTWORK", "GameFontNormalSmall")
	ui.subtitle:SetPoint("TOPLEFT", ui.title, "BOTTOMLEFT", 0, -2)
	ui.subtitle:SetPoint("RIGHT", f, "RIGHT", -100, 0)
	ui.subtitle:SetJustifyH("LEFT")
	ui.subtitle:SetWordWrap(false)

	-- Model for a new conversation (shown in New mode only)
	ui.modelBtn = Button(f, "Model", 240, function()
		local p = ModelPicker()
		if p:IsShown() then p:Hide() else FillPicker() p:Show() end
	end)
	ui.modelBtn:SetPoint("TOPRIGHT", f, "TOPRIGHT", -12, -30)
	ui.modelBtn:GetFontString():SetWidth(222)
	ui.modelBtn:GetFontString():SetWordWrap(false)
	Tooltip(ui.modelBtn, function(tt)
		tt:AddLine("Model for the new conversation", 1, 1, 1)
		tt:AddLine("Default uses your Hermes profile's model. The list shows the providers you are signed in to.", nil, nil, nil, true)
	end)

	-- Session list
	local list = CreateFrame("Frame", nil, f, "InsetFrameTemplate")
	list:SetPoint("TOPLEFT", f, "TOPLEFT", 8, -60)
	list:SetPoint("BOTTOMLEFT", f, "BOTTOMLEFT", 8, 30)
	list:SetWidth(LIST_WIDTH)
	ui.list = list
	local ls = CreateFrame("ScrollFrame", "HermesWoWListScroll", list, "UIPanelScrollFrameTemplate")
	ls:SetPoint("TOPLEFT", 4, -4)
	ls:SetPoint("BOTTOMRIGHT", -26, 4)
	ui.listScroll = ls
	ui.listContent = CreateFrame("Frame", nil, ls)
	ui.listContent:SetSize(LIST_WIDTH - 30, 1)
	ls:SetScrollChild(ui.listContent)
	ui.listEmpty = list:CreateFontString(nil, "ARTWORK", "GameFontDisable")
	ui.listEmpty:SetPoint("TOPLEFT", 10, -12)
	ui.listEmpty:SetPoint("RIGHT", -10, 0)
	ui.listEmpty:SetJustifyH("LEFT")
	ui.listEmpty:SetText("No sessions yet. They appear once the companion connects to Hermes.")

	-- Conversation
	local conv = f.Inset or CreateFrame("Frame", nil, f, "InsetFrameTemplate")
	conv:ClearAllPoints()
	conv:SetPoint("TOPLEFT", f, "TOPLEFT", LIST_WIDTH + 14, -60)
	conv:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -8, INPUT_HEIGHT + 38)
	ui.convInset = conv
	local cs = CreateFrame("ScrollFrame", "HermesWoWConvScroll", conv, "UIPanelScrollFrameTemplate")
	cs:SetPoint("TOPLEFT", 6, -4)
	cs:SetPoint("BOTTOMRIGHT", -26, 4)
	ui.convScroll = cs
	ui.convContent = CreateFrame("Frame", nil, cs)
	ui.convContent:SetSize(400, 1)
	cs:SetScrollChild(ui.convContent)

	-- Approval / clarify panel, above the input.
	local p = CreateFrame("Frame", nil, f, "InsetFrameTemplate")
	p:SetPoint("BOTTOMLEFT", f, "BOTTOMLEFT", LIST_WIDTH + 14, INPUT_HEIGHT + 40)
	p:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -8, INPUT_HEIGHT + 40)
	p:SetHeight(90)
	p.title = p:CreateFontString(nil, "ARTWORK", "GameFontNormal")
	p.title:SetPoint("TOPLEFT", 10, -8)
	p.text = p:CreateFontString(nil, "ARTWORK", "GameFontHighlightSmall")
	p.text:SetPoint("TOPLEFT", p.title, "BOTTOMLEFT", 0, -6)
	p.text:SetPoint("RIGHT", p, "RIGHT", -10, 0)
	p.text:SetJustifyH("LEFT")
	p.text:SetMaxLines(7)
	p:Hide()
	ui.prompt = p

	CreateInput(f)
	ui.send = Button(f, "Send", 84, DoSend)
	ui.send:SetPoint("TOPRIGHT", ui.inputBg, "TOPRIGHT", 92, -2)
	ui.stop = Button(f, "Stop", 84, function() if H.db.selected then H.Stop(H.db.selected) end end)
	ui.stop:SetPoint("TOP", ui.send, "BOTTOM", 0, -4)
	Tooltip(ui.stop, function(tt) tt:AddLine("Stop", 1, 1, 1) tt:AddLine("Interrupts the turn Hermes is running in this session.", nil, nil, nil, true) end)

	-- Game info toggle
	local cb = CreateFrame("CheckButton", "HermesWoWContextCheck", f, "UICheckButtonTemplate")
	cb:SetSize(22, 22)
	cb:SetPoint("TOPLEFT", ui.stop, "BOTTOMLEFT", -2, -1)
	local own = cb.Text or cb.text or _G["HermesWoWContextCheckText"]
	if type(own) == "table" and own.SetText then own:SetText("") end
	cb.label = cb:CreateFontString(nil, "ARTWORK", "GameFontNormalSmall")
	cb.label:SetPoint("LEFT", cb, "RIGHT", 0, 1)
	cb.label:SetText("Game info")
	cb:SetChecked(H.db.context and true or false)
	cb:SetScript("OnClick", function(self) H.db.context = self:GetChecked() and true or false end)
	Tooltip(cb, function(tt)
		tt:AddLine("Send game info", 1, 1, 1)
		tt:AddLine("Adds your character, zone, coordinates, target, money, group and quest log to each message. "
			.. "Only Hermes sees it; the conversation shows just what you typed. Right now it would send:", nil, nil, nil, true)
		for line in (H.GameContext() .. "\n"):gmatch("([^\n]*)\n") do
			if line ~= "" then tt:AddLine(H.Escape(line), 0.7, 0.7, 0.7, true) end
		end
	end)
	ui.contextCheck = cb

	-- Shift-click an item, spell or quest while typing in the message box to link it here.
	-- Hermes gets the link's tooltip text with the message.
	local lastLink, lastAt
	local function OnInsertLink(link)
		if type(link) ~= "string" or not (ui.input and ui.input:HasFocus()) then return end
		if link == lastLink and lastAt == GetTime() then return end  -- both hooks fired for one click
		lastLink, lastAt = link, GetTime()
		ui.input:Insert(link)
	end
	if ChatFrameUtil and ChatFrameUtil.InsertLink then hooksecurefunc(ChatFrameUtil, "InsertLink", OnInsertLink) end
	if ChatEdit_InsertLink then hooksecurefunc("ChatEdit_InsertLink", OnInsertLink) end

	-- Status bar
	ui.statusDot = f:CreateTexture(nil, "ARTWORK")
	ui.statusDot:SetSize(14, 14)
	ui.statusDot:SetPoint("BOTTOMLEFT", f, "BOTTOMLEFT", 12, 9)
	local sb = CreateFrame("Frame", nil, f)
	sb:SetPoint("BOTTOMLEFT", f, "BOTTOMLEFT", 28, 6)
	sb:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -200, 6)
	sb:SetHeight(20)
	ui.statusText = sb:CreateFontString(nil, "ARTWORK", "GameFontHighlightSmall")
	ui.statusText:SetPoint("LEFT", 0, 0)
	ui.statusText:SetPoint("RIGHT", 0, 0)
	ui.statusText:SetJustifyH("LEFT")
	ui.statusText:SetWordWrap(false)
	Tooltip(sb, function(tt)
		local _, text, detail = H.Status()
		tt:AddLine(text, 1, 1, 1)
		for _, l in ipairs(detail) do tt:AddLine(H.Escape(l), nil, nil, nil, true) end
	end)
	ui.reload = Button(f, "Reload UI", 90, function() ReloadUI() end)
	ui.reload:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -24, 6)
	Tooltip(ui.reload, function(tt) tt:AddLine("Reload UI", 1, 1, 1) tt:AddLine("Frees the reply slots. Unsent messages are kept and resent.", nil, nil, nil, true) end)
	ui.slots = f:CreateFontString(nil, "ARTWORK", "GameFontDisableSmall")
	ui.slots:SetPoint("RIGHT", ui.reload, "LEFT", -8, 0)

	LoadDraft()
	f:Hide()
end

---------------------------------------------------------------------------
-- Notifications: minimap button with an unread/working badge, a short toast and a sound
-- when a reply arrives or Hermes needs an answer.
---------------------------------------------------------------------------

local ICON = "Interface\\Icons\\INV_Misc_Book_09"
local TOAST_SECONDS = 8

local button, toast
local lastSound = -100

---------------------------------------------------------------------------
-- Minimap button (same layout as the classic tracking button)
---------------------------------------------------------------------------

local function Place()
	local a = math.rad(H.db.minimap.angle or 200)
	local r = (Minimap:GetWidth() / 2) + 5
	button:ClearAllPoints()
	button:SetPoint("CENTER", Minimap, "CENTER", math.cos(a) * r, math.sin(a) * r)
end

local function Counts()
	local unread, working, waiting = {}, 0, 0
	for _, s in ipairs(H.Sessions()) do
		if s.status == "running" then working = working + 1 end
		if s.status == "waiting" then waiting = waiting + 1 end
		if H.IsUnread(s.id) and not (H.IsShown() and s.id == H.db.selected) then unread[#unread + 1] = s end
	end
	return unread, working, waiting
end

function H.UpdateMinimap()
	if not button then return end
	if H.db.minimap.hide then button:Hide() return end
	button:Show()
	local unread, working, waiting = Counts()
	if waiting > 0 then
		button.badge:SetText("|cffffd100!|r")
	elseif #unread > 0 then
		button.badge:SetText("|cff20ff20" .. #unread .. "|r")
	elseif working > 0 then
		button.badge:SetText("|cff20ff20...|r")
	else
		button.badge:SetText("")
	end
	local level = H.Status()
	button.icon:SetDesaturated(level == "error")
end

local function NewestUnread()
	local unread = Counts()
	return unread[1]
end

local function CreateMinimapButton()
	local b = CreateFrame("Button", "HermesWoWMinimapButton", Minimap)
	b:SetSize(31, 31)
	b:SetFrameStrata("MEDIUM")
	b:SetFrameLevel(8)
	b:SetHighlightTexture("Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight")
	local overlay = b:CreateTexture(nil, "OVERLAY")
	overlay:SetSize(53, 53)
	overlay:SetTexture("Interface\\Minimap\\MiniMap-TrackingBorder")
	overlay:SetPoint("TOPLEFT")
	local bg = b:CreateTexture(nil, "BACKGROUND")
	bg:SetSize(20, 20)
	bg:SetTexture("Interface\\Minimap\\UI-Minimap-Background")
	bg:SetPoint("TOPLEFT", 7, -5)
	b.icon = b:CreateTexture(nil, "ARTWORK")
	b.icon:SetSize(17, 17)
	b.icon:SetTexture(ICON)
	b.icon:SetPoint("TOPLEFT", 7, -6)
	b.badge = b:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
	b.badge:SetPoint("BOTTOMRIGHT", -2, 3)
	b:RegisterForClicks("LeftButtonUp", "RightButtonUp")
	b:RegisterForDrag("LeftButton")
	b:SetScript("OnClick", function(_, mouse)
		if mouse == "RightButton" then
			local s = NewestUnread()
			if s then H.Open(s.id) end
			if not H.IsShown() then H.Toggle() end
		else
			H.Toggle()
		end
	end)
	b:SetScript("OnDragStart", function(self)
		self:SetScript("OnUpdate", function()
			local mx, my = Minimap:GetCenter()
			local px, py = GetCursorPosition()
			local scale = Minimap:GetEffectiveScale()
			H.db.minimap.angle = math.deg(math.atan2(py / scale - my, px / scale - mx))
			Place()
		end)
	end)
	b:SetScript("OnDragStop", function(self) self:SetScript("OnUpdate", nil) end)
	b:SetScript("OnEnter", function(self)
		GameTooltip:SetOwner(self, "ANCHOR_LEFT")
		GameTooltip:AddLine("Hermes")
		local level, text = H.Status()
		local c = level == "ok" and { 0.2, 1, 0.2 } or (level == "warn" and { 1, 0.82, 0 } or { 1, 0.25, 0.25 })
		GameTooltip:AddLine(text, c[1], c[2], c[3])
		local unread, working, waiting = Counts()
		if waiting > 0 then GameTooltip:AddLine(waiting .. " session(s) waiting for your answer", 1, 0.82, 0) end
		if working > 0 then GameTooltip:AddLine(working .. " session(s) working", 0.6, 0.6, 0.6) end
		for i, s in ipairs(unread) do
			if i > 5 then break end
			GameTooltip:AddLine("New: " .. H.Escape(s.title ~= "" and s.title or "Untitled"), 1, 1, 1)
		end
		GameTooltip:AddLine(" ")
		GameTooltip:AddLine("Left-click: open or close", 0.6, 0.6, 0.6)
		GameTooltip:AddLine("Right-click: open the newest reply", 0.6, 0.6, 0.6)
		GameTooltip:AddLine("Drag: move", 0.6, 0.6, 0.6)
		GameTooltip:Show()
	end)
	b:SetScript("OnLeave", GameTooltip_Hide)
	button = b
	Place()
	H.UpdateMinimap()
end

function H.ToggleMinimapButton()
	H.db.minimap.hide = not H.db.minimap.hide
	H.UpdateMinimap()
	H.Print("minimap button " .. (H.db.minimap.hide and "hidden" or "shown") .. ".")
end

---------------------------------------------------------------------------
-- Toast
---------------------------------------------------------------------------

local function CreateToast()
	local f = CreateFrame("Button", "HermesWoWToast", UIParent, "BackdropTemplate")
	f:SetSize(320, 58)
	f:SetPoint("TOP", UIParent, "TOP", 0, -150)
	f:SetFrameStrata("HIGH")
	f:SetBackdrop({
		bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
		edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
		tile = true, tileSize = 16, edgeSize = 16,
		insets = { left = 4, right = 4, top = 4, bottom = 4 },
	})
	f:SetBackdropColor(0, 0, 0, 0.85)
	f.icon = f:CreateTexture(nil, "ARTWORK")
	f.icon:SetSize(36, 36)
	f.icon:SetPoint("LEFT", 11, 0)
	f.icon:SetTexture(ICON)
	f.title = f:CreateFontString(nil, "ARTWORK", "GameFontNormal")
	f.title:SetPoint("TOPLEFT", f.icon, "TOPRIGHT", 10, -1)
	f.title:SetPoint("RIGHT", -10, 0)
	f.title:SetJustifyH("LEFT")
	f.text = f:CreateFontString(nil, "ARTWORK", "GameFontHighlightSmall")
	f.text:SetPoint("TOPLEFT", f.title, "BOTTOMLEFT", 0, -4)
	f.text:SetPoint("RIGHT", -10, 0)
	f.text:SetJustifyH("LEFT")
	f.text:SetWordWrap(false)
	f:SetScript("OnClick", function(self)
		self:Hide()
		if self.sid then H.Open(self.sid) end
		if not H.IsShown() then H.Toggle() end
	end)
	f:Hide()
	toast = f
end

local function ShowToast(title, text, sid)
	if not toast then CreateToast() end
	toast.title:SetText(title)
	toast.text:SetText(text)
	toast.sid = sid
	toast:Show()
	local token = {}
	toast.token = token
	local function expire()
		if toast.token ~= token then return end
		if toast:IsMouseOver() then C_Timer.After(2, expire) return end
		toast:Hide()
	end
	C_Timer.After(TOAST_SECONDS, expire)
end

---------------------------------------------------------------------------
-- Entry point: kind = "reply" or "prompt"
---------------------------------------------------------------------------

function H.Notify(kind, sid, title, detail)
	local cfg = H.db.notify
	local viewing = H.IsShown() and sid == H.db.selected
	if kind == "reply" and viewing then return end
	title = H.Escape(title ~= "" and title or "Untitled")
	if cfg.sound and GetTime() - lastSound > 3 and SOUNDKIT then
		lastSound = GetTime()
		local id = kind == "prompt" and SOUNDKIT.READY_CHECK or SOUNDKIT.TELL_MESSAGE
		if id then pcall(PlaySound, id, "Master") end
	end
	if cfg.toast and not viewing then
		if kind == "prompt" then
			ShowToast("|cffffd100Hermes needs you|r", title .. ": " .. (detail or "a question"), sid)
		else
			ShowToast("Hermes replied", title, sid)
		end
	end
	H.UpdateMinimap()
end

function H.CreateNotify()
	CreateMinimapButton()
end
