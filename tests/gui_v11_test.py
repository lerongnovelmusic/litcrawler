# -*- coding: utf-8 -*-
"""v1.1.0 新功能实机测试：影响因子真正生效、本地快速筛选、失败页签、可拖动日志、勾选样式。"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from litcrawler.core.config import Config
from litcrawler.gui import app as app_module
from litcrawler.gui.app import App


class _AutoMessagebox:
    @staticmethod
    def showinfo(*_a, **_k):
        return "ok"

    @staticmethod
    def showwarning(*_a, **_k):
        return "ok"

    @staticmethod
    def showerror(*_a, **_k):
        return "ok"

    @staticmethod
    def askyesno(*_a, **_k):
        return True


app_module.messagebox = _AutoMessagebox()
app_module.open_path = lambda *_a, **_k: None


def pump(app, timeout=180, cond=None):
    start = time.time()
    while time.time() - start < timeout:
        if cond is None:
            if not (app._searching or app.worker.running):
                break
        elif cond():
            break
        app.update()
        time.sleep(0.05)
    app.update()


tmp = Path(tempfile.mkdtemp(prefix="litcrawler_v11_"))
app = App(Config(tmp / "cfg"))
app.auto_confirm = True
app.geometry("1400x920+40+20")
app.update()

for name, var in app.source_vars.items():
    var.set(name in {"OpenAlex", "Europe PMC"})
app.keyword_var.set("deep learning medical image segmentation")
app.limit_var.set("30")
app.min_if_entry_var.set("8")
app.max_if_entry_var.set("")
app._apply_if_entry()
app.choose_journals = lambda: None

print("① 首次检索（最低影响因子 8）…")
app.start_search()
pump(app)
ifs = [p.impact_factor for p in app.papers]
print(f"   结果 {len(app.papers)} 条，影响因子 {ifs}")
assert app.papers, "没有结果"
bad = [p for p in app.papers if p.impact_factor is None or p.impact_factor < 8]
assert not bad, f"仍有低于 8 的文献：{[(p.title[:40], p.impact_factor) for p in bad]}"
print("   ✅ 所有结果影响因子都 ≥ 8")

fail_tab_text = app.nb.tab(1, "text")
print(f"   失败页签：{fail_tab_text!r}，记录 {len(app.failures)} 条")
assert len(app.failures) > 0, "失败记录为空"
assert app.fail_tree.get_children(), "失败表格没有渲染"
first = app.fail_tree.item(app.fail_tree.get_children()[0], "values")
print(f"   首条失败：{first[5]} / {first[6][:50]}")

print("② 收窄条件（最低影响因子 15）→ 应本地筛选…")
app.min_if_entry_var.set("15")
app._apply_if_entry()
t0 = time.time()
app.start_search()
pump(app)
elapsed = time.time() - t0
ifs2 = [p.impact_factor for p in app.papers]
print(f"   结果 {len(app.papers)} 条，用时 {elapsed:.2f}s，影响因子 {ifs2}")
assert app._last_was_local, "没有走本地筛选路径"
assert elapsed < 5, f"本地筛选太慢：{elapsed:.1f}s"
assert all(p.impact_factor and p.impact_factor >= 15 for p in app.papers)
print("   ✅ 本地筛选生效（未联网）")

print("③ 放宽条件（最低影响因子 3）→ 应重新联网…")
app.min_if_entry_var.set("3")
app._apply_if_entry()
app.start_search()
pump(app)
print(f"   结果 {len(app.papers)} 条，from_cache={app._last_result.from_cache}")
assert not app._last_result.from_cache, "放宽条件却复用了缓存"

print("④ 日志栏可拖动…")
sash_before = app.results_paned.sashpos(0)
app.results_paned.sashpos(0, sash_before - 80)
app.update()
sash_after = app.results_paned.sashpos(0)
print(f"   sash {sash_before} → {sash_after}")
assert sash_after != sash_before, "日志分隔线拖不动"

print("⑤ 数据源勾选框样式…")
cls = app.source_vars and None
for child in app.form_wrap.inner.winfo_children():
    pass
from litcrawler.gui.widgets import PALETTE
cbs = []


def walk(widget):
    for child in widget.winfo_children():
        if child.winfo_class() == "Checkbutton":
            cbs.append(child)
        walk(child)


walk(app.form_wrap.inner)
assert cbs, "找不到勾选框"
classes = {c.winfo_class() for c in cbs}
sample = cbs[0]
print(f"   勾选框类：{classes}，选中底色：{sample.cget('selectcolor')}")
assert sample.cget("selectcolor") == "#86efac"

print("⑥ 设置里可调失败记录条数…")
app.open_settings()
app.update()
dlg = None
for child in app.winfo_children():
    if type(child).__name__ == "SettingsDialog":
        dlg = child
assert dlg is not None
dlg.failure_limit_var.set(50)
dlg._save()
pump(app, timeout=10, cond=lambda: False)
print(f"   已保存：failure_limit={app.settings.failure_limit}")
assert app.settings.failure_limit == 50

print("⑦ 关窗")
app._on_close()
print("\nv1.1.0 新功能测试通过 ✅")
