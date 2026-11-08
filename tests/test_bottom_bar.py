"""The bottom bar's glow capsule (the default glass style) in the ERP and the owner console: the same layers in both,
the capsule of the tab left behind sinking away where it is, light from a touch, the theme's colours, and nothing
moving when the device asks for less motion. (How it looks on a phone is checked in a browser before a release.)"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = {"erp": ("erp/static/app.js", "erp/static/app.css"), "console": ("web-src/owner.js", "web-src/owner.css")}


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class GlowCapsule(unittest.TestCase):
    def test_both_apps_draw_the_same_layers_and_default_to_the_capsule(self):
        for app, (js, _css) in APPS.items():
            src = read(js)
            with self.subTest(app=app):
                layers = re.search(r"const DOCK_LAYERS = `(.*?)`;", src, re.S).group(1)
                self.assertLess(layers.index('class="bb-ghost"'), layers.index('class="bb-ind"'))   # the new capsule on top
                docks = re.search(r"const DOCKS = \(\) => \[(.*?)\]\];", src, re.S).group(1)
                self.assertEqual(re.findall(r'\["(\w+)", L2?\(', docks), ["halo", "spot", "bubble", "liquid", "classic"])
                self.assertIn(': "halo", where', src)                       # the default, and what an old "wave" becomes

    def test_a_new_tab_rises_in_and_the_one_left_behind_sinks_away_where_it_is(self):
        for app, (js, _css) in APPS.items():
            sync = re.search(r"function dockSync\(\) \{(.*?)\n\}", read(js), re.S).group(1)
            with self.subTest(app=app):
                self.assertIn('setProperty("--pi", prev)', sync)
                self.assertIn('"bb-leave"', sync)
                self.assertIn('"bb-rise"', sync)
                # cleared once faded, so showing the bar again (another style, a rotated tablet) does not replay it
                self.assertIn('bar._leaveT = setTimeout(() => bar.classList.toggle("bb-leave", false), 450)', sync)
                # the marker is placed by index (--bi), never slid across the bar
                self.assertNotIn("transition", sync)

    def test_escape_puts_the_capsule_back_on_the_page_in_use(self):
        for app, (js, _css) in APPS.items():
            with self.subTest(app=app):
                self.assertIn('addEventListener("keydown", e => { if (e.key === "Escape") setTimeout(dockSync, 40); });', read(js))
        # the ERP's Escape also closes an open alerts or account menu
        self.assertIn('if (e.key === "Escape") { closeDrawer(); closeMenus(); } });', read("erp/static/app.js"))

    def test_a_touch_lights_up_inside_the_button_touched(self):
        for app, (js, css) in APPS.items():
            src, style = read(js), read(css)
            with self.subTest(app=app):
                self.assertIn('document.addEventListener("pointerdown"', src)
                self.assertIn('"bb-tap"', src)
                rule = re.search(r"\.bb-new>\.btn::after\{(.*?)\}", style, re.S).group(1)
                self.assertIn("inset:3px", rule)
                self.assertIn("pointer-events:none", rule)
                self.assertIn("var(--tx,50%) var(--ty,50%)", rule)

    def test_the_capsule_stays_inside_the_bar_and_takes_the_theme_colour(self):
        for app, (_js, css) in APPS.items():
            style = read(css)
            with self.subTest(app=app):
                cap = re.search(r"\.dock-halo \.bb-ind i,\.dock-halo \.bb-ghost i\{(.*?)\}", style, re.S).group(1)
                self.assertIn("top:7px;bottom:7px", cap)
                self.assertIn("overflow:hidden", cap)          # its sheen and foot light stay inside it
                self.assertIn("var(--dock-hl,", style)
                self.assertNotIn(".dock-wave", style)
                self.assertNotIn("[dir=rtl] html", style)               # html is the root: such a selector never matches

    def test_the_themes_bar_colour_keeps_its_labels_readable(self):
        js, css = read("erp/static/app.js"), read("erp/static/app.css")
        theme = re.search(r"function applyTheme\(th\) \{(.*?)\n\}", js, re.S).group(1)
        # the label colour is picked for the bar colour, and the glass shows that colour nearly solid behind it
        self.assertIn('de.dataset.dockbar = "custom"', theme)
        self.assertIn('st.setProperty("--dock-a", "90%")', theme)
        self.assertIn('"--dock-a"].forEach(v => st.removeProperty(v))', theme)
        self.assertIn('"tint", "dockbar"].forEach(k => { delete de.dataset[k]; })', theme)
        self.assertIn("var(--dock-bg,var(--panel)) var(--dock-a,58%),transparent)", css)
        # the classic bar (phones too) takes it and its labels follow; the liquid drop paints its own warm bar instead
        self.assertIn("background:color-mix(in srgb,var(--dock-bg,var(--panel)) 92%,transparent)", css)
        self.assertIn("html.dock-classic[data-dockbar] .bottombar>a:not(.on)", css)
        self.assertIn("html.dock-classic .bottombar>.on{color:var(--dock-hl,var(--brand))}", css)
        self.assertIn("html.dock-classic[data-dockbar] .bottombar>.on{color:color-mix(in srgb,var(--dock-hl,var(--brand)) 55%,var(--dock-ink))}", css)
        self.assertNotIn("html.dock-liquid[data-dockbar]", css)
        self.assertIn(".dock-liquid .bb-bg::before{content:\"\";position:absolute;inset:0;border-radius:26px;background:var(--warm)}", css)

    def test_every_console_highlight_preset_shows_on_light_glass(self):
        presets = re.search(r"const DOCK_COLORS = \[(.*?)\];", read("web-src/owner.js")).group(1)
        for c in re.findall(r'"#([0-9a-f]{6})"', presets):
            r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
            self.assertLess(0.2126 * r + 0.7152 * g + 0.0722 * b, 200, c)  # not near white

    def test_less_motion_means_no_animation_and_no_second_capsule(self):
        for app, (_js, css) in APPS.items():
            style = read(css)
            with self.subTest(app=app):
                rm = re.search(r"@media \(prefers-reduced-motion:reduce\)\{html\.dock-halo \.bb-ghost\{display:none!important\}(.*?)\}\}", style, re.S)
                self.assertIsNotNone(rm)
                listed = {s.strip() for s in rm.group(1).split("{")[0].split(",")}   # whole selectors, not substrings
                for sel in (".bb-ghost i", ".dock-halo .bb-ind i::before", ".dock-halo .bb-ind i::after", ".bottombar .bb-tap::after", ".bb-ind i"):
                    self.assertIn(sel, listed)
                self.assertIn("animation:none!important", rm.group(1))


class BarStyleSavedAndGhostPlaced(unittest.TestCase):
    def test_the_server_keeps_every_style_the_apps_offer(self):
        sys.path.insert(0, str(ROOT))
        from erp import chat
        from erp.db import Database
        db = Database(":memory:")
        db.x("INSERT INTO users(username,pw_hash) VALUES (?,?)", ("sara", "x"))
        uid = db.one("SELECT id FROM users WHERE username=?", ("sara",))["id"]
        for style in ("spot", "halo", "bubble", "liquid", "classic", "halo"):
            chat.save_profile(db, uid, {"prefs": {"dock_style": style}})
            self.assertEqual(chat.profile(db, uid)["prefs"]["dock_style"], style)
        chat.save_profile(db, uid, {"prefs": {"dock_style": "<b>"}})                   # anything else: kept as it was
        self.assertEqual(chat.profile(db, uid)["prefs"]["dock_style"], "halo")

    def test_the_style_selects_work_on_every_screen(self):
        js = read("erp/static/app.js")
        self.assertIn("S.GLOBAL_H.dockset = el => saveDock(", js)       # S.H is rebuilt from GLOBAL_H on each route
        self.assertNotIn("S.H.dockset", js)

    def test_the_console_ghost_has_as_many_columns_as_its_bar(self):
        css = read("web-src/owner.css")
        self.assertIn("html.dock-halo .bb-ghost{display:block;position:absolute;top:0;bottom:0;left:6px;width:calc((100% - 12px) / var(--n,5));", css)

    def test_pressing_the_tab_in_use_leaves_a_lifted_icon_in_place(self):
        for app, (_js, css) in APPS.items():
            with self.subTest(app=app):
                self.assertIn("html:not(.dock-classic) .bottombar>a:not(.on):active svg.i", read(css))
                self.assertNotIn("html:not(.dock-classic) .bottombar>a:active svg.i", read(css))

    def test_a_highlight_lost_in_the_bar_colour_falls_back_to_its_text(self):
        theme = re.search(r"function applyTheme\(th\) \{(.*?)\n\}", read("erp/static/app.js"), re.S).group(1)
        self.assertIn('if (hexOk(hl) && contrast(hl, th.dockbar) < 3) st.setProperty("--dock-hl", ink); }', theme)
        self.assertIn('contrast(th.dockbar, "#ffffff") >= contrast(th.dockbar, "#1e2430")', theme)


if __name__ == "__main__":
    unittest.main()
