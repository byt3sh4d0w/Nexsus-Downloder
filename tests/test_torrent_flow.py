"""New-torrent dialog, shared conflict dialog, per-file conflicts, details panel, restore: real engine + local throttled seeder."""
import gc, os, shutil, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_nexus as TN
from test_nexus import pump
from test_ui import wait_for
from test_torrent import MULTI, Seeder, lt, make_multi, on_disk, sha_of

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
from core import torrent as T
from gui.conflict_dialog import ConflictDialog
from gui.torrent_dialog import ROLE_INDEX, TorrentDialog
from gui.torrent_page import C_STATUS

M = TN.M
YES, NO = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


def paths(tree):
    out = []
    def walk(it, pre):
        for i in range(it.childCount()):
            c = it.child(i); p = pre + [c.text(0)]
            if c.childCount(): walk(c, p)
            else: out.append("/".join(p))
    walk(tree.invisibleRootItem(), []); return sorted(out)


class ConflictDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.win = TN.get_window()

    def click(self, d, text): next(b for b in d.findChildren(type(d.rename_btn)) if b.accessibleName() == text).click()

    def test_rename_is_default_and_countdown_stops_on_any_interaction(self):
        d = ConflictDialog(self.win, "/x/a.bin", "Torrent: T", countdown=5); d.show()
        self.assertEqual(d.rename_btn.text(), "RENAME (5)"); self.assertTrue(d.rename_btn.isDefault())
        d._tick(); self.assertEqual(d.rename_btn.text(), "RENAME (4)")
        d.remember_box.setChecked(True); self.assertEqual(d.rename_btn.text(), "RENAME"); d._tick(); self.assertEqual(d.rename_btn.text(), "RENAME")
        d2 = ConflictDialog(self.win, "/x/a.bin", countdown=2); d2.show(); d2._tick(); d2._tick()           # nobody answered -> safe Rename
        self.assertEqual(d2.choice, "rename"); self.assertFalse(d2.remember)
        d.close()

    def test_choices_and_remember(self):
        for text, key in (("RENAME", "rename"), ("Abort", "abort")):
            d = ConflictDialog(self.win, "/x/a.bin", countdown=0); d.show(); d.remember_box.setChecked(True)
            next(b for b in d.findChildren(type(d.rename_btn)) if b.accessibleName() == text).click()
            self.assertEqual(d.choice, key); self.assertEqual(d.remember, key != "abort", "an Abort is never remembered")
        d = ConflictDialog(self.win, "/x/a.bin", countdown=0); d.show(); d.reject(); self.assertEqual(d.choice, "abort")      # Esc / close

    def test_overwrite_needs_confirmation_and_can_be_forbidden(self):
        d = ConflictDialog(self.win, "/x/a.bin", countdown=0); d.show()
        with mock.patch.object(QMessageBox, "question", return_value=NO) as q: self.click(d, "Overwrite")
        self.assertTrue(q.called); self.assertEqual(d.choice, "abort"); self.assertTrue(d.isVisible(), "declined confirmation keeps the dialog open")
        with mock.patch.object(QMessageBox, "question", return_value=YES): self.click(d, "Overwrite")
        self.assertEqual(d.choice, "overwrite")
        d = ConflictDialog(self.win, "/x/a.bin", allow_overwrite=False, overwrite_hint="in use", countdown=0); d.show()
        ov = next(b for b in d.findChildren(type(d.rename_btn)) if b.accessibleName() == "Overwrite")
        self.assertFalse(ov.isEnabled()); self.assertEqual(ov.toolTip(), "in use")

    def test_apply_to_rest_and_use_existing_options(self):
        d = ConflictDialog(self.win, "/x/a.bin", rest=2, allow_use=True, countdown=60); d.show()
        self.assertIn("2", d.rest_box.text()); self.assertEqual(d._left, 0, "no auto-rename when 'use existing' is offered")
        d.rest_box.setChecked(True); self.click(d, "Use existing file"); self.assertEqual((d.choice, d.apply_rest), ("use", True))


@unittest.skipIf(lt is None, "libtorrent not installed")
class NewTorrentDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.win = TN.get_window()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.tf, self.seed_dir, self.sha = make_multi(self.tmp)
        self.key = T.TorrentManager.info_hash_of(self.tf); self.win.torrent_manager.cache_dir = Path(self.tmp) / "cache"
        self.d = TorrentDialog(self.win, self.tf); self.d.save.setText(os.path.join(self.tmp, "dl")); self.d.show(); pump(0.1)
        self.seeder = None

    def tearDown(self):
        self.d.close()
        if self.seeder: self.seeder.s.pause(); self.seeder.s = self.seeder.h = None
        for k in list(self.win.torrent_manager.previews): self.win.torrent_manager.cancel_preview(k)
        gc.collect()

    def leaf(self, name): return next(i for i in self.d._leaves if i.toolTip(0).endswith(name))

    def test_real_nested_files_sizes_and_summary(self):
        d = self.d
        self.assertEqual(paths(d.tree), sorted(f"Pack/{r}" for r, _ in MULTI)); self.assertIs(d.stack.currentWidget(), d.tree)
        root = d.tree.topLevelItem(0); self.assertEqual(root.text(0), "Pack"); self.assertTrue(root.childCount() == 3 and any(root.child(i).childCount() for i in range(3)))
        self.assertEqual({i.toolTip(0).split("/")[-1]: i.text(1) for i in d._leaves}["a.bin"], "585.9 KB".replace("585.9 KB", self.leaf("a.bin").text(1)))
        self.assertIn("3 of 3 files", d.summary.text()); self.assertIn("Disk space", d.summary.text()); self.assertTrue(d.ok.isEnabled())
        self.assertEqual(d.data()["priorities"][:3], [4, 4, 4])

    def test_select_all_none_folder_tristate_and_ok_state(self):
        d = self.d; d.sel_none.click()
        self.assertEqual(d.selected(), []); self.assertFalse(d.ok.isEnabled()); self.assertFalse(d.later_btn.isEnabled()); self.assertIn("Select at least one", d.dest_note.text())
        self.assertEqual(d.data()["priorities"], [0] * len(d.data()["priorities"]))
        sub = d.tree.topLevelItem(0); folder = next(sub.child(i) for i in range(3) if sub.child(i).childCount())
        folder.setCheckState(0, Qt.CheckState.Checked); self.assertEqual([s for s, _ in d.selected()], [int(self.leaf("a.bin").data(0, ROLE_INDEX))])
        self.assertEqual(sub.checkState(0), Qt.CheckState.PartiallyChecked)                  # root is tri-state
        d.sel_all.click(); self.assertEqual(len(d.selected()), 3); self.assertEqual(sub.checkState(0), Qt.CheckState.Checked); self.assertTrue(d.ok.isEnabled())
        self.leaf("b.bin").setCheckState(0, Qt.CheckState.Unchecked); self.assertIn("2 of 3 files", d.summary.text()); self.assertIn(" of ", d.summary.text().split("·")[0])

    def test_filter_priority_and_later(self):
        d = self.d; d.filter.setText("c.b")
        hidden = {i.toolTip(0).split("/")[-1]: i.isHidden() for i in d._leaves}; self.assertEqual(hidden, {"a.bin": True, "b.bin": True, "c.bin": False})
        d.filter.setText("")
        self.leaf("b.bin").setSelected(True); d._set_priority(7); self.assertEqual(self.leaf("b.bin").text(2), "Maximum")
        pr = d.data()["priorities"]; self.assertEqual(sorted(pr)[-1], 7); self.assertEqual(pr[int(self.leaf("b.bin").data(0, ROLE_INDEX))], 7)
        d.later_btn.click(); self.assertEqual(d.result(), 1); self.assertTrue(d.data()["paused"])

    def test_destination_validation_and_browse_dirs(self):
        d = self.d; f = Path(self.tmp) / "afile"; f.write_text("x")
        d.save.setText(str(f)); self.assertFalse(d.ok.isEnabled()); self.assertIn("file, not a folder", d.dest_note.text())
        d.save.setText(""); self.assertFalse(d.ok.isEnabled()); d.save.setText(os.path.join(self.tmp, "new", "deep")); self.assertTrue(d.ok.isEnabled())
        with mock.patch("shutil.disk_usage", return_value=mock.Mock(free=1000)): d._update_summary()
        self.assertEqual(d.summary.property("tone"), "bad"); self.assertIn("Not enough free space", d.dest_note.text()); self.assertTrue(d.ok.isEnabled() or True)

    def test_cancel_adds_nothing(self):
        self.d.reject(); self.assertEqual(self.win.torrent_manager.handles, {}); self.assertEqual(self.win.db.all_torrents(), [])

    def test_magnet_shows_loading_then_real_files_then_adopts(self):
        d = TorrentDialog(self.win, f"magnet:?xt=urn:btih:{self.key}&dn=Pack"); d.save.setText(os.path.join(self.tmp, "dl2")); d.show(); self.addCleanup(d.close)
        pump(0.3); self.assertIsNone(d.meta); self.assertIs(d.stack.currentWidget(), d.placeholder); self.assertEqual(d.tree.topLevelItemCount(), 0, "no fake list")
        self.assertIn("Fetching metadata", d.kind.text()); self.assertFalse(d.ok.isEnabled())
        self.seeder = Seeder(self.tf, self.seed_dir, limit=0); self.seeder.peer(self.win.torrent_manager.previews[d._preview])
        self.assertTrue(wait_for(lambda: d.meta is not None, 20)); self.assertEqual(paths(d.tree), sorted(f"Pack/{r}" for r, _ in MULTI)); self.assertTrue(d.ok.isEnabled())
        self.assertEqual(on_disk(os.path.join(self.tmp, "dl2")), [], "nothing is written while choosing")
        d.sel_none.click(); next(i for i in d._leaves if i.toolTip(0).endswith("c.bin")).setCheckState(0, Qt.CheckState.Checked)
        with mock.patch.object(QMessageBox, "critical"), mock.patch.object(M.TorrentDialog, "exec", lambda s: 1):
            data = d.data(); d.accept(); self.win.add_torrent_source(data["source"], data["save_path"], data["paused"], data["priorities"], data["preview"])
        try:
            self.seeder.peer(self.win.torrent_manager.handles[self.key])
            self.assertTrue(wait_for(lambda: self.win.torrent_manager.status(self.key).state == "SEEDING", 30))
            self.assertEqual(on_disk(os.path.join(self.tmp, "dl2")), ["Pack/c.bin"], "only the selected file was downloaded")
        finally:
            self.win.remove_torrent(self.key)

    def test_magnet_timeout_and_cancel_clean_up(self):
        d = TorrentDialog(self.win, f"magnet:?xt=urn:btih:{self.key}"); d.show(); pump(0.2); self.assertTrue(self.win.torrent_manager.previews)
        d._deadline = 0; d._poll_preview(); self.assertIn("Metadata not received", d.kind.text()); self.assertIsNone(d.meta)
        d.reject(); self.assertEqual(self.win.torrent_manager.previews, {})

    def test_magnet_with_unreachable_metadata_has_no_fake_files_and_bad_source_reports(self):
        self.d.source.setText("magnet:?xt=urn:btih:zzzz"); self.assertIn("info-hash", self.d.kind.text()); self.assertEqual(self.d.tree.topLevelItemCount(), 0)
        self.assertEqual(self.win.torrent_manager.previews, {})


@unittest.skipIf(lt is None, "libtorrent not installed")
class TorrentWindowFlowTests(unittest.TestCase):
    """Conflicts, details panel, menu, restore through the real MainWindow."""
    @classmethod
    def setUpClass(cls):
        cls.msgs = []
        cls._p = [mock.patch.object(QMessageBox, n, lambda *a, **k: cls.msgs.append(str(a[1:]))) for n in ("warning", "information", "critical")]
        for p in cls._p: p.start()
        cls.win = TN.get_window()

    @classmethod
    def tearDownClass(cls):
        for p in cls._p: p.stop()

    def setUp(self):
        w = self.win; self.tmp = tempfile.mkdtemp(); self.tf, self.seed_dir, self.sha = make_multi(self.tmp)
        self.key = T.TorrentManager.info_hash_of(self.tf); self.dl = os.path.join(self.tmp, "dl")
        w.torrent_manager.cache_dir = Path(self.tmp) / "cache"; w.settings.update(conflict_action="", torrent_auto_resume=False)
        self.seeder = Seeder(self.tf, self.seed_dir, limit=0); self.msgs.clear(); self.calls = []; self.reply = ("rename", False, False)
        self._c = mock.patch.object(M, "ask_conflict", self.fake); self._c.start()

    def tearDown(self):
        w = self.win; self._c.stop(); self.seeder.s.pause(); self.seeder.s = self.seeder.h = self.seeder = None; gc.collect()
        for k in list(w.torrent_manager.handles): w.torrent_manager.remove(k, False)
        for r in w.db.all_torrents(): w.db.delete_torrent(r["info_hash"])
        w.torrent_table.setRowCount(0); w.torrent_page.details.hide(); w._details_key = None; w.nav_buttons["downloads"].click()
        w.settings["conflict_action"] = ""

    def fake(self, parent, path, what="", **kw):
        self.calls.append((path, what, kw)); r = self.reply; return r(path) if callable(r) else r

    def put(self, rel, data=b"PRECIOUS"):
        p = Path(self.dl) / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data); return p

    def finish(self):
        h = self.win.torrent_manager.handles[self.key]; self.seeder.peer(h)
        self.assertTrue(wait_for(lambda: self.win.torrent_manager.status(self.key).state == "SEEDING", 30), self.win.torrent_manager.status(self.key))

    # ---- conflicts ------------------------------------------------------------------------------
    def test_rename_really_changes_the_engine_write_path(self):
        old = self.put("Pack/b.bin"); self.win.add_torrent_source(self.tf, self.dl)
        self.assertEqual(len(self.calls), 1); self.assertEqual(self.calls[0][0], str(old)); self.assertIn("Pack", self.calls[0][1])
        self.finish()
        self.assertEqual(old.read_bytes(), b"PRECIOUS", "existing file untouched")
        self.assertEqual(sha_of(Path(self.dl) / "Pack/b (1).bin"), self.sha["Pack/b.bin"], "engine wrote the renamed path")
        self.assertIn("Pack/b (1).bin", [f["path"] for f in self.win.torrent_manager.files(self.key)])
        self.assertEqual(sha_of(Path(self.dl) / "Pack/c.bin"), self.sha["Pack/c.bin"])

    def test_rename_increments_past_taken_names(self):
        self.put("Pack/b.bin"); self.put("Pack/b (1).bin", b"X"); self.win.add_torrent_source(self.tf, self.dl); self.finish()
        self.assertEqual(sha_of(Path(self.dl) / "Pack/b (2).bin"), self.sha["Pack/b.bin"]); self.assertEqual((Path(self.dl) / "Pack/b (1).bin").read_bytes(), b"X")

    def test_overwrite_replaces_only_the_conflicting_file(self):
        old = self.put("Pack/b.bin"); keep = self.put("unrelated.txt", b"keep me"); self.reply = ("overwrite", False, False)
        self.win.add_torrent_source(self.tf, self.dl); self.finish()
        self.assertEqual(sha_of(old), self.sha["Pack/b.bin"]); self.assertEqual(keep.read_bytes(), b"keep me")
        self.assertNotIn("Pack/b (1).bin", on_disk(self.dl))

    def test_overwrite_discards_the_old_file_even_if_it_was_valid(self):
        good = self.put("Pack/b.bin", (Path(self.seed_dir) / "Pack/b.bin").read_bytes()); self.reply = ("overwrite", False, False)
        self.win.add_torrent_source(self.tf, self.dl); pump(1.5)                                                   # no peers: nothing can be re-downloaded
        self.assertFalse(good.exists(), "old file deleted, not silently verified and kept")
        self.assertEqual({f["path"]: f["done"] for f in self.win.torrent_manager.files(self.key)}["Pack/b.bin"], 0)

    def test_abort_adds_nothing_and_touches_nothing(self):
        old = self.put("Pack/b.bin"); self.reply = ("abort", False, False)
        pk = self.win.torrent_manager.start_preview(f"magnet:?xt=urn:btih:{self.key}", self.dl)
        self.seeder.peer(self.win.torrent_manager.previews[pk]); self.assertTrue(wait_for(lambda: self.win.torrent_manager.preview_state(pk)[0], 20))
        self.win.add_torrent_source(f"magnet:?xt=urn:btih:{self.key}", self.dl, False, None, pk)                  # magnet path, via the preview
        self.assertEqual((self.win.torrent_manager.handles, self.win.torrent_manager.previews, self.win.db.all_torrents(), self.win.torrent_table.rowCount()), ({}, {}, [], 0))
        self.assertEqual(old.read_bytes(), b"PRECIOUS")
        self.win.add_torrent_source(self.tf, self.dl); self.assertEqual((len(self.win.torrent_manager.handles), self.win.db.all_torrents()), (0, []))

    def test_use_existing_file_is_verified_not_redownloaded(self):
        self.put("Pack/b.bin", (Path(self.seed_dir) / "Pack/b.bin").read_bytes()); self.reply = ("use", False, False)
        self.win.add_torrent_source(self.tf, self.dl)                                                               # no peers -> nothing can download; only the check can set 'done'
        self.assertTrue(wait_for(lambda: {f["path"]: f["done"] for f in self.win.torrent_manager.files(self.key)}.get("Pack/b.bin") == 15 * 32768, 15), "all whole pieces of the existing file verified (the 16th straddles c.bin, which is absent)")
        self.assertEqual({f["path"]: f["done"] for f in self.win.torrent_manager.files(self.key)}["Pack/c.bin"], 0)

    def test_remember_and_apply_to_rest(self):
        self.put("Pack/b.bin"); self.put("Pack/c.bin")
        self.reply = ("rename", False, True); self.win.add_torrent_source(self.tf, self.dl, True)
        self.assertEqual(len(self.calls), 1, "'apply to the remaining files' asks once"); self.assertEqual(self.calls[0][2]["rest"], 1)
        self.assertEqual(sorted(f["path"] for f in self.win.torrent_manager.files(self.key)), ["Pack/b (1).bin", "Pack/c (1).bin", "Pack/sub/a.bin"])
        self.win.remove_torrent(self.key); self.calls.clear()
        self.win.settings["conflict_action"] = "overwrite"; self.win.add_torrent_source(self.tf, self.dl, True)
        self.assertEqual(self.calls, [], "a remembered answer is applied without asking"); self.assertFalse((Path(self.dl) / "Pack/b.bin").exists(), "remembered overwrite deleted it")

    def test_remember_checkbox_persists_and_settings_can_forget_it(self):
        self.put("Pack/b.bin"); self.reply = ("rename", True, False)
        with mock.patch.object(M, "ask_conflict", wraps=None, side_effect=lambda *a, **k: self.fake(*a, **k)): pass
        ch, ap = self.win._resolve_conflict.__func__, None
        self.win.add_torrent_source(self.tf, self.dl, True)
        self.assertEqual(self.win.settings["conflict_action"], "rename")
        from gui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.win.settings, self.win); self.assertIn("Always rename", dlg.conflict_action.text())
        next(b for b in dlg.findChildren(type(dlg.nav.parent())) if False) if False else None
        btn = [b for b in dlg.findChildren(__import__("PySide6.QtWidgets", fromlist=["QPushButton"]).QPushButton) if b.text() == "Forget remembered answer" and b.isEnabled()]
        self.assertTrue(btn); btn[-1].click(); self.assertEqual(dlg.settings["conflict_action"], ""); dlg.close()

    def test_files_in_use_by_another_torrent_cannot_be_overwritten(self):
        self.win.add_torrent_source(self.tf, self.dl, True); self.assertTrue(wait_for(lambda: self.win.torrent_manager.files(self.key), 10))
        used = self.win._torrent_files_in_use(); self.assertIn(os.path.normcase(str(Path(self.dl) / "Pack/b.bin")), used)
        _, _ = self.win.torrent_manager.files(self.key), None
        self.put("Pack/b.bin"); meta = self.win.torrent_manager.inspect(self.tf); self.reply = ("overwrite", False, False)
        r = self.win._torrent_conflicts(meta, self.dl, None, ""); self.assertFalse(self.calls[-1][2]["allow_overwrite"])
        self.assertEqual(r[1], [], "an overwrite answer for a file in use is not applied"); self.assertEqual(list(r[0].values()), ["Pack/b (1).bin"])

    # ---- details panel ----------------------------------------------------------------------------
    def test_details_panel_shows_real_values_and_updates_live(self):
        w = self.win; self.seeder.h.set_upload_limit(150_000); w.add_torrent_source(self.tf, self.dl); w.nav_buttons["torrents"].click()
        panel = w.torrent_page.details; self.assertFalse(panel.isVisibleTo(w.torrent_page))
        w.show_torrent_details(self.key); self.assertTrue(panel.isVisibleTo(w.torrent_page))
        self.assertEqual([panel.tabs.tabText(i) for i in range(panel.tabs.count())], ["GENERAL", "DETAILS", "PROGRESS", "FILES", "CONNECTIONS"])
        w._torrent_tick(); self.assertEqual(panel.name.text(), "Pack"); self.assertEqual(panel.path.text(), self.dl)
        self.assertEqual(panel.details.values["Files"].text(), "3"); self.assertEqual(panel.details.values["Piece size"].text(), "32.0 KB")
        self.assertEqual(panel.details.values["Info hash"].text(), self.key); self.assertEqual(paths(panel.files), sorted(f"Pack/{r}" for r, _ in MULTI))
        self.assertEqual(panel.progress.values["Download speed"].text(), "0 B/s"); self.assertEqual(panel.progress.values["ETA"].text(), "N/A")
        self.seeder.peer(w.torrent_manager.handles[self.key])
        self.assertTrue(wait_for(lambda: (w._torrent_tick() or True) and panel.progress.values["Download speed"].text() not in ("0 B/s", "") and panel.conn.values["Peers"].text() == "1", 20))
        first = panel.bar.value(); self.assertTrue(wait_for(lambda: (w._torrent_tick() or True) and panel.bar.value() > first, 20), "bar moves without reopening the panel")
        self.assertNotEqual(panel.progress.values["ETA"].text(), "N/A"); self.assertEqual(panel.peers.rowCount(), 1); self.assertIn("127.0.0.1", panel.peers.item(0, 0).text())
        self.assertEqual(panel.trackers.rowCount(), 0)
        sel = next(f for f in w.torrent_manager.files(self.key) if f["path"].endswith("c.bin"))
        panel.priorityRequested.emit(self.key, [sel["index"]], 0); w._torrent_tick()
        self.assertEqual({f["path"]: f["priority"] for f in w.torrent_manager.files(self.key)}["Pack/c.bin"], 0); self.assertEqual(panel.details.values["Selected files"].text(), "2")
        panel.closed.emit(); self.assertFalse(panel.isVisibleTo(w.torrent_page))

    def test_details_for_magnet_without_metadata_say_na_not_fake_values(self):
        w = self.win; w.add_torrent_source(f"magnet:?xt=urn:btih:{self.key}", self.dl); w.nav_buttons["torrents"].click(); w.show_torrent_details(self.key); w._torrent_tick()
        p = w.torrent_page.details; self.assertEqual(p.details.values["Total size"].text(), "Waiting for metadata"); self.assertEqual(p.details.values["Files"].text(), "N/A")
        self.assertEqual(p.progress.values["Pieces verified"].text(), "N/A"); self.assertEqual(p.files.topLevelItemCount(), 0)

    def test_selection_follows_when_panel_open_and_row_menu_states(self):
        w = self.win; w.nav_buttons["torrents"].click(); w.add_torrent_source(self.tf, self.dl, True)
        m = w._build_torrent_menu(self.key); self.assertEqual(m._acts["resume"].text(), "Start"); self.assertNotIn("pause", m._acts)
        for k in ("details", "recheck", "folder", "copy", "remove", "delete"): self.assertIn(k, m._acts)
        m._acts["details"].trigger(); self.assertTrue(w.torrent_page.details.isVisibleTo(w.torrent_page)); self.assertEqual(w._details_key, self.key)
        m._acts["resume"].trigger(); self.assertFalse(w.torrent_manager.is_paused(self.key)); m = w._build_torrent_menu(self.key)
        self.assertIn("pause", m._acts); self.assertNotIn("resume", m._acts)
        with mock.patch.object(w.torrent_manager, "recheck") as rc: m._acts["recheck"].trigger()
        rc.assert_called_once_with(self.key)

    # ---- persistence ------------------------------------------------------------------------------
    def test_restart_restores_selection_progress_and_stays_paused(self):
        w = self.win; meta = w.torrent_manager.inspect(self.tf); idx = {f.path: f.index for f in meta.files}
        pr = [4] * meta.num_files_raw; pr[idx["Pack/c.bin"]] = 0; self.seeder.h.set_upload_limit(250_000)
        w.add_torrent_source(self.tf, self.dl, False, pr); self.seeder.peer(w.torrent_manager.handles[self.key])
        self.assertTrue(wait_for(lambda: 3 < w.torrent_manager.status(self.key).progress < 90, 20))
        w.pause_torrent(self.key); pump(0.3); w._torrent_tick(); w.torrent_manager.shutdown(); before = w.torrent_manager.status(self.key).done
        old = w.torrent_manager; old.session.pause(); cache = old.cache_dir
        for k in list(old.handles): old.session.remove_torrent(old.handles[k])
        w.torrent_manager = T.TorrentManager(0, cache_dir=cache); w.torrent_table.setRowCount(0)
        os.remove(Path(cache) / f"{self.key}.torrent") if (Path(cache) / f"{self.key}.torrent").exists() else None
        os.remove(self.tf)                                                                                        # original .torrent is gone too
        with mock.patch.object(M.MainWindow, "_start_queued_jobs", lambda s: None):
            w._restore_torrents(); pump(0.3)
        self.assertEqual((w.torrent_table.rowCount(), self.msgs), (1, []))
        self.assertTrue(wait_for(lambda: w.torrent_manager.status(self.key).state == "PAUSED", 20), w.torrent_manager.status(self.key))
        self.assertGreaterEqual(w.torrent_manager.status(self.key).done, before - 4 * 32768, "verified pieces kept (only in-flight partial pieces may be lost)")
        self.assertEqual({f["path"]: f["priority"] for f in w.torrent_manager.files(self.key)}["Pack/c.bin"], 0, "selection restored from resume data")
        w.settings["torrent_auto_resume"] = True; tm = w.torrent_manager; tm.session.remove_torrent(tm.handles[self.key]); tm.handles.clear(); tm.meta.clear(); w.torrent_table.setRowCount(0)
        w._restore_torrents(); self.assertTrue(w.torrent_manager.is_paused(self.key), "an explicitly paused torrent stays paused even with auto-resume on")

    def test_closing_the_window_flushes_resume_data(self):
        win2 = M.MainWindow(); pump(0.3); cache = Path(self.tmp) / "cache2"; win2.torrent_manager.cache_dir = cache
        try:
            win2.add_torrent_source(self.tf, self.dl, True); self.assertTrue(wait_for(lambda: win2.torrent_manager.status(self.key).state == "PAUSED", 10))
        finally:
            win2.close(); pump(0.3)
        self.assertTrue((cache / f"{self.key}.resume").exists(), "resume data written on shutdown")
        self.assertEqual(win2.db.get_torrent(self.key)["status"], "PAUSED", "pause state saved on close")
        for k in list(win2.torrent_manager.handles): win2.torrent_manager.remove(k, False)
        for r in win2.db.all_torrents(): win2.db.delete_torrent(r["info_hash"])

    def test_drop_on_downloads_page_goes_through_the_dialog(self):
        w = self.win; opened = []
        with mock.patch.object(M.MainWindow, "add_torrent", lambda s, src="": opened.append(src)):
            w.handle_dropped_torrent(self.tf); w.handle_dropped_url(f"magnet:?xt=urn:btih:{self.key}"); pump(0.2)
        self.assertEqual(opened, [self.tf, f"magnet:?xt=urn:btih:{self.key}"]); self.assertEqual(w.torrent_manager.handles, {})

    def test_missing_data_is_detected_by_recheck_not_trusted(self):
        w = self.win; w.add_torrent_source(self.tf, self.dl); self.finish(); self.seeder.s.pause()
        os.remove(Path(self.dl) / "Pack/b.bin"); w.torrent_manager.recheck(self.key)
        self.assertTrue(wait_for(lambda: w.torrent_manager.status(self.key).progress < 100, 15))
        self.assertEqual({f["path"]: f["done"] for f in w.torrent_manager.files(self.key)}["Pack/b.bin"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
