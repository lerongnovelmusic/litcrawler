# -*- coding: utf-8 -*-
"""GUI 实机测试：创建窗口 → 真实检索 → 勾选 → 下载 → 导出，全程驱动事件循环。"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from litcrawler.core.config import Config
from litcrawler.gui import app as app_module
from litcrawler.gui.app import App
from litcrawler.gui.dialogs import JournalPickerDialog
from litcrawler.gui.widgets import TextDialog


# 自动化模式：所有确认框自动回答，不弹窗、不打开资源管理器
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

tmp = Path(tempfile.mkdtemp(prefix="litcrawler_gui_"))
cfg = Config(tmp / "cfg")
app = App(cfg)
app.auto_confirm = True
app.geometry("1300x820")
app.update()

# 只勾 OpenAlex + Europe PMC，避免测试太慢
for name, var in app.source_vars.items():
    var.set(name in {"OpenAlex", "Europe PMC"})
app.keyword_var.set("deep learning medical image segmentation")
app.limit_var.set("30")
app.min_if_entry_var.set("5")
app.max_if_entry_var.set("")
app._apply_if_entry()
app.choose_journals = lambda: None  # 不在测试里弹窗

print("触发检索…")
app.start_search()

deadline = time.time() + 180
while (app._searching or app.worker.running) and time.time() < deadline:
    app.update()
    time.sleep(0.05)
app.update()

print(f"表格行数：{len(app.item_ids)}  结果数：{len(app.papers)}")
assert app.papers, "GUI 检索没有返回结果"

# 勾选所有带 PDF 的
app.select_downloadable()
app.update()
print(f"已勾选：{len(app.selected)} 条")

# 文件菜单导出
out_csv = tmp / "results.csv"
from litcrawler.gui.app import _write_results

_write_results(out_csv, app.papers, "csv")
text = out_csv.read_text("utf-8-sig")
print(f"CSV 导出 {len(text.splitlines())} 行，表头：{text.splitlines()[0][:60]}")

# 下载前两条
app.dir_var.set(str(tmp / "dl"))
papers = [p for p in app.papers if p.has_pdf][:2]
app.selected = {p.key for p in papers}
app._update_selection_label()
app.download_btn.invoke()
deadline = time.time() + 240
while (app._downloading or app.worker.running) and time.time() < deadline:
    app.update()
    time.sleep(0.05)
app.update()

files = list((tmp / "dl").glob("*.pdf")) if (tmp / "dl").exists() else []
print(f"下载完成，文件数：{len(files)}")
for f in files:
    assert f.read_bytes()[:5] == b"%PDF-", f
    print(f"  {f.name[:66]:66s} {f.stat().st_size/1024:7.0f} KB")

# 历史记录
print(f"搜索记录条数：{len(app.config_obj.history.entries)}")
assert app.config_obj.history.entries, "搜索记录未写入"
print("  记录：", app.config_obj.history.entries[0].summary())

# 设置窗口能否打开（先做，避免与模态详情窗口的 grab 冲突）
app.open_settings()
app.update()
for child in app.winfo_children():
    if type(child).__name__ == "SettingsDialog":
        child._close()
app.update()
print("设置窗口正常")

# 期刊选择对话框能否打开
dlg = JournalPickerDialog(app, app.config_obj.journals, app.journals)
app.update()
dlg._cancel()
app.update()
print("期刊选择窗口正常")

# 详情窗口（模态，放在最后）
app.table.selection_set(app.item_ids[0])
app.show_details()
app.update()
opened = [c for c in app.winfo_children() if isinstance(c, TextDialog)]
assert opened, "详情窗口未打开"
print("详情窗口正常")
for child in opened:
    child.destroy()
app.update()

app.log("GUI 测试结束")
app.update()
app._on_close()
print("\nGUI 端到端测试通过 ✅")
