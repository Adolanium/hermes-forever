-- Minimal WoW API stub for running HermesWoW outside the game (tests/sim.py).
-- Frames are permissive mocks: unknown methods are no-ops, the ones the addon reads back
-- (text, size, shown state, scripts, texture colors and points) are recorded.

local function noop() end
local Mock = {}
local FIELDS = { Inset = true, PortraitContainer = true, portrait = true, TitleText = true, ScrollBar = true }
local mt = { __index = function(_, k) if FIELDS[k] or not k:match("^%u") then return Mock[k] end return Mock[k] or noop end }

STUB = { frames = {}, events = {}, tickers = {}, timers = {}, chat = {} }

local function New(kind, name, parent)
	local o = setmetatable({ _kind = kind, _shown = true, _text = "", _w = 400, _h = 20, _scripts = {},
		_textures = {}, _parent = parent }, mt)
	if name then _G[name] = o end
	return o
end
STUB.New = New

function Mock.CreateTexture(self) local t = New("Texture", nil, self) table.insert(self._textures, t) return t end
function Mock.CreateFontString(self) return New("FontString", nil, self) end
function Mock.SetText(self, t) self._text = t or "" end
function Mock.GetText(self) return self._text end
function Mock.Insert(self, t) self._text = self._text .. t end
function Mock.Show(self) self._shown = true if self._scripts.OnShow then self._scripts.OnShow(self) end end
function Mock.Hide(self) local was = self._shown self._shown = false if was and self._scripts.OnHide then self._scripts.OnHide(self) end end
function Mock.SetShown(self, v) if v then self:Show() else self:Hide() end end
function Mock.IsShown(self) return self._shown end
function Mock.IsVisible(self) return self._shown end
function Mock.SetWidth(self, w) self._w = w end
function Mock.SetHeight(self, h) self._h = h end
function Mock.SetSize(self, w, h) self._w, self._h = w, h end
function Mock.GetWidth(self) return self._w end
function Mock.GetHeight(self) return self._h end
function Mock.GetStringHeight(self) return 14 * (1 + select(2, (self._text or ""):gsub("\n", ""))) end
function Mock.GetStringWidth(self) return 6 * #(self._text or "") end
function Mock.GetVerticalScrollRange() return 0 end
function Mock.GetVerticalScroll() return 0 end
function Mock.GetFontString(self) self._fs = self._fs or New("FontString", nil, self) return self._fs end
function Mock.GetPoint() return "CENTER", nil, "CENTER", 0, 0 end
function Mock.HasFocus(self) return self._focus or false end
function Mock.SetFocus(self) self._focus = true end
function Mock.ClearFocus(self) self._focus = false end
function Mock.GetFrameLevel() return 1 end
function Mock.GetEffectiveScale() return 1 end
function Mock.SetColorTexture(self, r, g, b) self._color = { r, g, b } end
function Mock.SetAlpha(self, a) self._alpha = a end
function Mock.SetChecked(self, v) self._checked = v and true or false end
function Mock.GetChecked(self) return self._checked or false end
function Mock.SetPoint(self, p, a, b, c, d)
	-- Strip cells: SetPoint("TOPLEFT", strip, "TOPLEFT", x, y)
	if type(c) == "number" and type(d) == "number" then self._x, self._y = c, d end
end
function Mock.SetScript(self, name, fn) self._scripts[name] = fn end
function Mock.GetScript(self, name) return self._scripts[name] end
function Mock.RegisterEvent(self, e) STUB.events[e] = STUB.events[e] or {} table.insert(STUB.events[e], self) end

function CreateFrame(kind, name, parent, template)
	local f = New(kind, name, parent)
	table.insert(STUB.frames, f)
	return f
end

function STUB.Fire(event, ...)
	for _, f in ipairs(STUB.events[event] or {}) do
		if f._scripts.OnEvent then f._scripts.OnEvent(f, event, ...) end
	end
end

UIParent = New("Frame", "UIParent")
GameTooltip = New("GameTooltip", "GameTooltip")
DEFAULT_CHAT_FRAME = New("Frame")
function DEFAULT_CHAT_FRAME.AddMessage(_, msg) table.insert(STUB.chat, msg) end
for _, f in ipairs({ "ChatFontNormal", "GameFontNormal", "GameFontHighlight", "GameFontDisable", "GameFontDisableSmall",
	"GameFontNormalSmall", "GameFontHighlightSmall", "GameFontGreenSmall", "GameFontHighlightLarge" }) do
	_G[f] = New("Font", f)
end
UISpecialFrames = {}
StaticPopupDialogs = {}
STUB.popups = {}
function StaticPopup_Show(which, _, _, data) table.insert(STUB.popups, { which = which, data = data }) end
function IsMouseButtonDown() return false end
SlashCmdList = {}
tinsert = table.insert
function GameTooltip_Hide() end
function IsShiftKeyDown() return false end
function ReloadUI() STUB.reloaded = true end
function InCombatLockdown() return false end
function GetPhysicalScreenSize() return 1890, 993 end
function StopSound() end
function date(fmt, t) return os.date(fmt, t) end
function time() return os.time() end

C_Timer = {
	NewTicker = function(interval, fn) table.insert(STUB.tickers, fn) return {} end,
	After = function(_, fn) table.insert(STUB.timers, fn) end,
}

Minimap = New("Frame", "Minimap")
SOUNDKIT = { TELL_MESSAGE = 3081, READY_CHECK = 8960 }
STUB.sounds = {}
function PlaySound(id, channel) table.insert(STUB.sounds, id) return true end
function GetCursorPosition() return 0, 0 end
