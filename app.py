#!/usr/bin/env python3
"""
Xbox Soundtrack Studio
======================
"""
import os
import sys
import shutil
import tempfile
import threading
import traceback

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine"))

import qcow2
import fatx
import soundtrack as st

APP_NAME = "Xbox Soundtrack Studio"
TDATA_MUSIC = "/TDATA/fffe0000/music"

class Task(threading.Thread):
    def __init__(self, fn, on_done, on_error, on_progress=None):
        super().__init__(daemon=True)
        self.fn = fn
        self.on_done = on_done
        self.on_error = on_error
        self.on_progress = on_progress
    def run(self):
        try:
            result = self.fn(self._progress)
            self.on_done(result)
        except Exception as e:
            self.on_error(e, traceback.format_exc())
    def _progress(self, *args):
        if self.on_progress: self.on_progress(*args)

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("760x620")
        self.minsize(680, 560)
        self.image_path = None
        self.is_qcow2 = False
        self.work_dir = tempfile.mkdtemp(prefix="xbst_")
        self.raw_path = None
        self.music_folder = None
        self.codec = tk.StringVar(value="wmav1")  # Force wmav1 Default!
        self.busy = False
        self._check_ffmpeg()
        self._build_ui()

    def _check_ffmpeg(self):
        self.have_ffmpeg = shutil.which("ffmpeg") and shutil.which("ffprobe")

    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}
        header = tk.Frame(self, bg="#1d6b2e")
        header.pack(fill="x")
        tk.Label(header, text=APP_NAME, fg="white", bg="#1d6b2e", font=("Helvetica", 18, "bold")).pack(side="left", padx=16, pady=12)
        self.status_dot = tk.Label(header, text="", fg="white", bg="#1d6b2e", font=("Helvetica", 11))
        self.status_dot.pack(side="right", padx=16)

        s1 = ttk.LabelFrame(self, text="1.  Open Xbox HDD image (.qcow2 or .img)")
        s1.pack(fill="x", **pad)
        self.image_label = tk.Label(s1, text="No image loaded", anchor="w", fg="#555")
        self.image_label.pack(side="left", fill="x", expand=True, padx=8, pady=8)
        ttk.Button(s1, text="Open…", command=self.open_image).pack(side="right", padx=8, pady=8)

        s2 = ttk.LabelFrame(self, text="2.  Existing soundtracks on E:")
        s2.pack(fill="both", expand=True, **pad)
        cols = ("name", "tracks", "length")
        self.tree = ttk.Treeview(s2, columns=cols, show="tree headings", height=8)
        self.tree.heading("#0", text="")
        self.tree.heading("name", text="Soundtrack / Track")
        self.tree.heading("tracks", text="Tracks")
        self.tree.heading("length", text="Length")
        self.tree.column("#0", width=24, stretch=False)
        self.tree.column("tracks", width=70, anchor="center")
        self.tree.column("length", width=80, anchor="center")
        self.tree.pack(fill="both", expand=True, side="left", padx=8, pady=8)

        s2btns = tk.Frame(s2)
        s2btns.pack(side="right", fill="y", padx=8, pady=8)
        ttk.Button(s2btns, text="New empty soundtrack…",
                   command=self.create_empty_soundtrack).pack(fill="x", pady=2)
        ttk.Separator(s2btns, orient="horizontal").pack(fill="x", pady=6)
        tk.Label(s2btns, text="Soundtrack:", font=("Helvetica", 9, "bold")).pack(anchor="w")
        ttk.Button(s2btns, text="Rename", command=self.rename_selected).pack(fill="x", pady=2)
        ttk.Button(s2btns, text="Remove", command=self.remove_selected).pack(fill="x", pady=2)
        ttk.Button(s2btns, text="Add tracks…", command=self.add_tracks_to_selected).pack(fill="x", pady=2)
        ttk.Separator(s2btns, orient="horizontal").pack(fill="x", pady=6)
        tk.Label(s2btns, text="Track:", font=("Helvetica", 9, "bold")).pack(anchor="w")
        ttk.Button(s2btns, text="Remove track", command=self.remove_track_selected).pack(fill="x", pady=2)
        ttk.Button(s2btns, text="Move track…", command=self.move_track_selected).pack(fill="x", pady=2)

        s3 = ttk.LabelFrame(self, text="3.  Add a soundtrack")
        s3.pack(fill="x", **pad)
        row1 = tk.Frame(s3)
        row1.pack(fill="x", padx=8, pady=4)
        self.music_label = tk.Label(row1, text="No audio folder selected", anchor="w", fg="#555")
        self.music_label.pack(side="left", fill="x", expand=True)
        ttk.Button(row1, text="Choose audio folder…", command=self.choose_music).pack(side="right")

        row2 = tk.Frame(s3)
        row2.pack(fill="x", padx=8, pady=4)
        tk.Label(row2, text="Soundtrack name:").pack(side="left")
        self.name_entry = ttk.Entry(row2, width=28)
        self.name_entry.pack(side="left", padx=8)
        tk.Label(row2, text="WMA codec:").pack(side="left", padx=(16, 4))
        ttk.Combobox(row2, textvariable=self.codec, values=["wmav1", "wmav2"], width=8, state="readonly").pack(side="left")

        ttk.Button(s3, text="Convert + Inject into image", command=self.inject).pack(anchor="e", padx=8, pady=8)

        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", padx=12, pady=(2, 0))
        self.log = tk.Text(self, height=7, bg="#0e1411", fg="#7CFC9A", font=("Menlo", 10), state="disabled")
        self.log.pack(fill="both", expand=False, padx=12, pady=8)

        s4 = tk.Frame(self)
        s4.pack(fill="x", padx=12, pady=(0, 12))
        self.saveas_btn = ttk.Button(s4, text="Save As…", command=self.save_image_as, state="disabled")
        self.saveas_btn.pack(side="right", padx=(6, 0))
        self.save_btn = ttk.Button(s4, text="Save", command=self.save_image, state="disabled")
        self.save_btn.pack(side="right")

    def logmsg(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.update_idletasks()

    def set_busy(self, busy, status=""):
        self.busy = busy
        self.status_dot.config(text=status)
        self.update_idletasks()

    def open_image(self):
        if self.busy: return
        path = filedialog.askopenfilename(filetypes=[("Xbox HDD images", "*.qcow2 *.img *.raw"), ("All files", "*.*")])
        if not path: return
        self.image_path = path
        self.is_qcow2 = qcow2.is_qcow2(path)
        self.image_label.config(text=os.path.basename(path))
        self.logmsg(f"Opening {os.path.basename(path)}…")
        self.set_busy(True, "Loading…")
        self.progress.config(mode="determinate", value=0, maximum=100)

        def work(progress):
            raw = os.path.join(self.work_dir, "disk.img")
            if self.is_qcow2:
                r = qcow2.Qcow2Reader(self.image_path)
                r.extract_to_raw(raw, progress=lambda c, t: progress(c, t))
                r.close()
            else:
                shutil.copy2(self.image_path, raw)
            return raw

        def done(raw):
            self.raw_path = raw
            self.progress.config(value=100)
            self.logmsg("Image loaded.")
            self.set_busy(False, "Ready")
            self.refresh_soundtracks()
            self.save_btn.config(state="normal")
            self.saveas_btn.config(state="normal")

        def err(e, tb):
            self.set_busy(False, "Error")
            messagebox.showerror("Failed", str(e))

        Task(work, lambda r: self.after(0, done, r), lambda e, tb: self.after(0, err, e, tb),
             on_progress=lambda c, t: self.after(0, lambda: self.progress.config(maximum=t, value=c))).start()

    def refresh_soundtracks(self):
        for i in self.tree.get_children(): self.tree.delete(i)
        if not self.raw_path: return
        try:
            img = fatx.XboxImage(self.raw_path, writable=False)
            if "E" not in img.available_partitions(): return
            e = img.partition("E")
            self._populate_from_stdb(e)
            img.close()
        except Exception:
            pass

    def _populate_from_stdb(self, partition):
        # Use the full reader so we get per-track song ids for the child rows.
        try:
            soundtracks = self._read_existing_soundtracks(partition)
        except Exception:
            return
        for s in soundtracks:
            st_id = s.get("st_id")
            songs = s.get("songs", [])
            total_ms = sum(x["duration_ms"] for x in songs)
            parent_iid = f"st:{st_id}"
            self.tree.insert("", "end", iid=parent_iid,
                             text="",
                             values=(s["name"], len(songs),
                                     f"{total_ms//60000}:{total_ms//1000%60:02d}"),
                             open=False)
            for pos, song in enumerate(songs):
                dur = song["duration_ms"]
                track_iid = f"tr:{st_id}:{song['song_id']}"
                self.tree.insert(parent_iid, "end", iid=track_iid,
                                 text="",
                                 values=(f"{pos+1}. {song['name']}", "",
                                         f"{dur//60000}:{dur//1000%60:02d}"))

    def _selected_st_id(self):
        """Return the st_id of the selected soundtrack (or the parent of a
        selected track), or None."""
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        try:
            if iid.startswith("st:"):
                return int(iid[3:])
            if iid.startswith("tr:"):
                return int(iid.split(":")[1])
        except (ValueError, IndexError):
            return None
        return None

    def _selected_track(self):
        """Return (st_id, song_id) if a track row is selected, else None."""
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        if not iid.startswith("tr:"):
            return None
        try:
            _, st_id, song_id = iid.split(":")
            return int(st_id), int(song_id)
        except (ValueError, IndexError):
            return None

    def add_tracks_to_selected(self):
        if self.busy or not self.raw_path:
            return
        st_id = self._selected_st_id()
        if st_id is None:
            messagebox.showinfo("No selection", "Select a soundtrack to add tracks to.")
            return
        paths = filedialog.askopenfilenames(
            title="Choose audio files to add",
            filetypes=[("Audio", "*.mp3 *.wma *.flac *.ogg *.aac *.m4a *.wav"),
                       ("All files", "*.*")])
        if not paths:
            return
        self.set_busy(True, "Adding tracks…")
        self.progress.config(mode="determinate", value=0, maximum=100)

        def work(progress):
            return self._do_add_tracks(st_id, list(paths), progress)

        def done(_):
            self.logmsg("Tracks added. Don't forget to Save image.")
            self.set_busy(False, "Ready")
            self.refresh_soundtracks()

        def err(e, tb):
            self.set_busy(False, "Error")
            self.logmsg("ERROR: " + str(e))
            messagebox.showerror("Add tracks failed", str(e))

        Task(work, lambda r: self.after(0, done, r),
             lambda e, tb: self.after(0, err, e, tb)).start()

    def remove_track_selected(self):
        if self.busy or not self.raw_path:
            return
        sel = self._selected_track()
        if sel is None:
            messagebox.showinfo("No track selected",
                                "Expand a soundtrack and select a single track to remove.")
            return
        st_id, song_id = sel
        track_name = self.tree.item(f"tr:{st_id}:{song_id}", "values")[0]
        if not messagebox.askyesno("Remove track",
                                   f"Remove track '{track_name}'?\n\n"
                                   "The remaining tracks will be renumbered.\n"
                                   "Remember to Save the image afterwards."):
            return
        self.set_busy(True, "Removing track…")

        def work(progress):
            return self._do_remove_track(st_id, song_id)

        def done(_):
            self.logmsg("Track removed. Don't forget to Save image.")
            self.set_busy(False, "Ready")
            self.refresh_soundtracks()

        def err(e, tb):
            self.set_busy(False, "Error")
            self.logmsg("ERROR: " + str(e))
            messagebox.showerror("Remove track failed", str(e))

        Task(work, lambda r: self.after(0, done, r),
             lambda e, tb: self.after(0, err, e, tb)).start()

    def remove_selected(self):
        if self.busy or not self.raw_path:
            return
        st_id = self._selected_st_id()
        if st_id is None:
            messagebox.showinfo("No selection", "Select a soundtrack to remove.")
            return
        name = self.tree.item(f"st:{st_id}", "values")[0]
        if not messagebox.askyesno("Remove soundtrack",
                                   f"Remove '{name}' and delete its audio files?\n\n"
                                   "Remember to Save the image afterwards."):
            return
        self.set_busy(True, "Removing…")

        def work(progress):
            return self._do_remove(st_id, progress)

        def done(_):
            self.logmsg(f"Removed '{name}'. Don't forget to Save image.")
            self.set_busy(False, "Ready")
            self.refresh_soundtracks()

        def err(e, tb):
            self.set_busy(False, "Error")
            self.logmsg("ERROR: " + str(e))
            messagebox.showerror("Remove failed", str(e))

        Task(work, lambda r: self.after(0, done, r),
             lambda e, tb: self.after(0, err, e, tb)).start()

    def rename_selected(self):
        if self.busy or not self.raw_path:
            return
        st_id = self._selected_st_id()
        if st_id is None:
            messagebox.showinfo("No selection", "Select a soundtrack to rename.")
            return
        current = self.tree.item(f"st:{st_id}", "values")[0]
        from tkinter import simpledialog
        new_name = simpledialog.askstring("Rename soundtrack",
                                          "New name:", initialvalue=current, parent=self)
        if not new_name or new_name == current:
            return
        self.set_busy(True, "Renaming…")

        def work(progress):
            return self._do_rename(st_id, new_name)

        def done(_):
            self.logmsg(f"Renamed to '{new_name}'. Don't forget to Save image.")
            self.set_busy(False, "Ready")
            self.refresh_soundtracks()

        def err(e, tb):
            self.set_busy(False, "Error")
            self.logmsg("ERROR: " + str(e))
            messagebox.showerror("Rename failed", str(e))

        Task(work, lambda r: self.after(0, done, r),
             lambda e, tb: self.after(0, err, e, tb)).start()

    def create_empty_soundtrack(self):
        if self.busy or not self.raw_path:
            return
        from tkinter import simpledialog
        name = simpledialog.askstring("New empty soundtrack",
                                      "Soundtrack name:", parent=self)
        if not name:
            return
        self.set_busy(True, "Creating…")

        def work(progress):
            return self._do_create_empty(name)

        def done(_):
            self.logmsg(f"Created '{name}'. Use 'Add tracks…' to put songs in it.")
            self.set_busy(False, "Ready")
            self.refresh_soundtracks()

        def err(e, tb):
            self.set_busy(False, "Error")
            self.logmsg("ERROR: " + str(e))
            messagebox.showerror("Create failed", str(e))

        Task(work, lambda r: self.after(0, done, r),
             lambda e, tb: self.after(0, err, e, tb)).start()

    def move_track_selected(self):
        if self.busy or not self.raw_path:
            return
        sel = self._selected_track()
        if sel is None:
            messagebox.showinfo("No track selected",
                                "Expand a soundtrack and select a single track to move.")
            return
        src_st_id, song_id = sel

        # Gather candidate destinations (all soundtracks except the source).
        dests = []  # (st_id, name)
        for iid in self.tree.get_children(""):
            if not iid.startswith("st:"):
                continue
            sid = int(iid[3:])
            if sid == src_st_id:
                continue
            dests.append((sid, self.tree.item(iid, "values")[0]))

        if not dests:
            messagebox.showinfo("No destination",
                                "There's no other soundtrack to move this track to.\n"
                                "Create another soundtrack first.")
            return

        dst_st_id = self._pick_destination(dests)
        if dst_st_id is None:
            return

        track_name = self.tree.item(f"tr:{src_st_id}:{song_id}", "values")[0]
        self.set_busy(True, "Moving track…")

        def work(progress):
            return self._do_move_track(src_st_id, song_id, dst_st_id)

        def done(ok):
            if ok:
                self.logmsg(f"Moved '{track_name}'. Don't forget to Save image.")
            self.set_busy(False, "Ready")
            self.refresh_soundtracks()

        def err(e, tb):
            self.set_busy(False, "Error")
            self.logmsg("ERROR: " + str(e))
            messagebox.showerror("Move failed", str(e))

        Task(work, lambda r: self.after(0, done, r),
             lambda e, tb: self.after(0, err, e, tb)).start()

    def _pick_destination(self, dests):
        """Modal dialog to choose a destination soundtrack. Returns st_id or None."""
        dialog = tk.Toplevel(self)
        dialog.title("Move track to…")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        tk.Label(dialog, text="Move this track to which soundtrack?",
                 padx=16, pady=10).pack()

        listbox = tk.Listbox(dialog, height=min(10, len(dests)), width=40)
        for _sid, nm in dests:
            listbox.insert("end", nm)
        listbox.pack(padx=16, pady=4)
        listbox.selection_set(0)

        result = {"st_id": None}

        def confirm():
            idx = listbox.curselection()
            if idx:
                result["st_id"] = dests[idx[0]][0]
            dialog.destroy()

        def cancel():
            dialog.destroy()

        btns = tk.Frame(dialog)
        btns.pack(pady=10)
        ttk.Button(btns, text="Move", command=confirm).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=cancel).pack(side="left", padx=4)
        listbox.bind("<Double-Button-1>", lambda e: confirm())

        self.wait_window(dialog)
        return result["st_id"]

    def choose_music(self):
        folder = filedialog.askdirectory()
        if not folder: return
        self.music_folder = folder
        self.music_label.config(text=folder)
        if not self.name_entry.get():
            self.name_entry.insert(0, os.path.basename(folder)[:47])

    def inject(self):
        if self.busy or not self.raw_path or not self.music_folder: return
        st_name = (self.name_entry.get().strip() or os.path.basename(self.music_folder))[:47]
        self.set_busy(True, "Injecting…")
        self.progress.config(mode="determinate", value=0, maximum=100)

        def work(progress): return self._do_inject(st_name, progress)
        def done(_):
            self.logmsg("Injection complete.")
            self.set_busy(False, "Ready")
            self.refresh_soundtracks()
        def err(e, tb):
            self.set_busy(False, "Error")
            self.logmsg(str(e))

        Task(work, lambda r: self.after(0, done, r), lambda e, tb: self.after(0, err, e, tb)).start()

    def _alloc_st_id(self, e, all_soundtracks):
        """Pick a fresh soundtrack id that collides with neither the database
        nor any folder already on disk."""
        existing_st_ids = [s.get("st_id", 0) for s in all_soundtracks]
        try:
            existing_dirs = [int(x["name"], 16) for x in e.list_dir(TDATA_MUSIC) if x["is_dir"]]
        except fatx.FatxError:
            existing_dirs = []
        all_used = set(existing_st_ids + existing_dirs)
        return max(all_used) + 1 if all_used else 1

    def _do_inject(self, st_name, progress):
        img = fatx.XboxImage(self.raw_path, writable=True)
        e = img.partition("E")

        all_soundtracks = self._read_existing_soundtracks(e)

        new_st_id = self._alloc_st_id(e, all_soundtracks)
        folder_num = new_st_id

        audio_files = sorted(f for f in os.listdir(self.music_folder) if os.path.splitext(f)[1].lower() in st.AUDIO_EXTS)
        if not audio_files: raise RuntimeError("No valid audio files.")

        tmp_wma = os.path.join(self.work_dir, "wma")
        os.makedirs(tmp_wma, exist_ok=True)

        songs = []
        # The composite song id is (st_id << 16) | local_index, and the WMA
        # filename is that composite. The LOCAL index must start at 0 for each
        # soundtrack — the dashboard expects each folder's songs to begin at
        # local 0. Seeding this from the global nextSongId (as before) made the
        # 2nd+ soundtrack's local ids start above 0, shifting every track by one
        # and silencing the last. Start local at 0 every time.
        local_id = 0
        for i, af in enumerate(audio_files):
            src = os.path.join(self.music_folder, af)

            # Upper 16 bits = folder/soundtrack id, lower 16 bits = local track index
            composite_id = ((new_st_id & 0xFFFF) << 16) | (local_id & 0xFFFF)

            dst = os.path.join(tmp_wma, f"{composite_id:08x}.wma")
            if st.convert_to_wma(src, dst, codec=self.codec.get()):
                songs.append({"name": st.clean_track_name(af), "duration_ms": st.ffprobe_duration_ms(dst),
                              "song_id": composite_id, "wma_path": dst})
                self.logmsg(f"  {af} → {composite_id:08x}.wma")
                local_id += 1
            progress(int((i + 1) / len(audio_files) * 60), 100)

        folder_path = f"{TDATA_MUSIC}/{folder_num:04x}"
        self.logmsg(f"Writing WMA files into E:{folder_path}…")
        for s in songs:
            with open(s["wma_path"], "rb") as fh:
                e.write_file(f"{folder_path}/{s['song_id']:08x}.wma", fh.read())

        all_soundtracks.append({"name": st_name, "songs": songs, "st_id": new_st_id})
        e.write_file(f"{TDATA_MUSIC}/ST.DB", st.build_stdb(all_soundtracks))
        img.close()
        return True

    def _do_remove(self, st_id, progress=None):
        """Remove the soundtrack with the given st_id: delete its folder and
        rebuild ST.DB from the remaining soundtracks."""
        img = fatx.XboxImage(self.raw_path, writable=True)
        e = img.partition("E")
        try:
            all_soundtracks = self._read_existing_soundtracks(e)
            remaining = [s for s in all_soundtracks if s.get("st_id") != st_id]
            if len(remaining) == len(all_soundtracks):
                self.logmsg(f"No soundtrack with id {st_id:04x} found.")
                img.close()
                return False

            # Delete that soundtrack's WMA folder (named by st_id in hex)
            folder = f"{TDATA_MUSIC}/{st_id:04x}"
            try:
                e.delete_dir(folder)
                self.logmsg(f"Deleted folder E:{folder}")
            except fatx.FatxError as ex:
                self.logmsg(f"(folder {st_id:04x} not found on disk: {ex})")

            # Rebuild ST.DB (or remove it entirely if nothing left)
            if remaining:
                e.write_file(f"{TDATA_MUSIC}/ST.DB", st.build_stdb(remaining))
            else:
                try:
                    e.delete_file(f"{TDATA_MUSIC}/ST.DB")
                except fatx.FatxError:
                    pass
            self.logmsg(f"Rebuilt ST.DB with {len(remaining)} soundtrack(s).")
        finally:
            img.close()
        return True

    def _do_rename(self, st_id, new_name):
        """Rename a soundtrack: change its name and rebuild ST.DB. No file changes."""
        img = fatx.XboxImage(self.raw_path, writable=True)
        e = img.partition("E")
        try:
            all_soundtracks = self._read_existing_soundtracks(e)
            found = False
            for s in all_soundtracks:
                if s.get("st_id") == st_id:
                    s["name"] = new_name[:47]
                    found = True
                    break
            if not found:
                self.logmsg(f"No soundtrack with id {st_id:04x} found.")
                img.close()
                return False
            e.write_file(f"{TDATA_MUSIC}/ST.DB", st.build_stdb(all_soundtracks))
            self.logmsg(f"Renamed soundtrack {st_id:04x} to '{new_name}'.")
        finally:
            img.close()
        return True

    def _do_create_empty(self, st_name):
        """Create a new soundtrack with no tracks. Tracks are added later via
        'Add tracks…'."""
        img = fatx.XboxImage(self.raw_path, writable=True)
        e = img.partition("E")
        try:
            all_soundtracks = self._read_existing_soundtracks(e)
            new_st_id = self._alloc_st_id(e, all_soundtracks)
            # Create the (empty) folder so the soundtrack has a home on disk.
            e.ensure_dir(f"{TDATA_MUSIC}/{new_st_id:04x}")
            all_soundtracks.append({"name": st_name[:47], "songs": [], "st_id": new_st_id})
            e.write_file(f"{TDATA_MUSIC}/ST.DB", st.build_stdb(all_soundtracks))
            self.logmsg(f"Created empty soundtrack '{st_name}' (id {new_st_id:04x}).")
        finally:
            img.close()
        return True

    def _do_move_track(self, src_st_id, song_id, dst_st_id):
        """Move a track from one soundtrack to another. The track's audio bytes
        are carried over (no re-conversion); it is renumbered into the
        destination's local index space and physically rewritten there."""
        if src_st_id == dst_st_id:
            return False
        img = fatx.XboxImage(self.raw_path, writable=True)
        e = img.partition("E")
        try:
            all_soundtracks = self._read_existing_soundtracks(e)
            src = next((s for s in all_soundtracks if s.get("st_id") == src_st_id), None)
            dst = next((s for s in all_soundtracks if s.get("st_id") == dst_st_id), None)
            if not src or not dst:
                self.logmsg("Source or destination soundtrack not found.")
                return False

            moving = next((s for s in src["songs"] if s["song_id"] == song_id), None)
            if not moving:
                self.logmsg("Track not found in source.")
                return False

            # Read the moving track's bytes from the source folder before any rewrite.
            src_folder = f"{TDATA_MUSIC}/{src_st_id:04x}"
            track_bytes = e.read_file(f"{src_folder}/{song_id:08x}.wma")

            # Stage the bytes in a temp file so we can feed it through the
            # destination rewrite as a "new" track.
            tmp_dir = os.path.join(self.work_dir, "move")
            os.makedirs(tmp_dir, exist_ok=True)
            tmp_path = os.path.join(tmp_dir, f"move_{song_id:08x}.wma")
            with open(tmp_path, "wb") as fh:
                fh.write(track_bytes)

            # Rebuild source without the moved track (renumbers remaining tracks).
            src_keep = [s for s in src["songs"] if s["song_id"] != song_id]
            if src_keep:
                src["songs"] = self._rewrite_soundtrack(
                    e, src_st_id,
                    [{"name": s["name"], "duration_ms": s["duration_ms"],
                      "existing_song_id": s["song_id"]} for s in src_keep])
            else:
                # Source becomes empty — wipe its folder, keep an empty entry.
                try:
                    e.delete_dir(src_folder)
                except fatx.FatxError:
                    pass
                e.ensure_dir(src_folder)
                src["songs"] = []

            # Rebuild destination with its existing tracks plus the moved one.
            dst_desired = [{"name": s["name"], "duration_ms": s["duration_ms"],
                            "existing_song_id": s["song_id"]} for s in dst["songs"]]
            dst_desired.append({"name": moving["name"],
                                "duration_ms": moving["duration_ms"],
                                "wma_path": tmp_path})
            dst["songs"] = self._rewrite_soundtrack(e, dst_st_id, dst_desired)

            e.write_file(f"{TDATA_MUSIC}/ST.DB", st.build_stdb(all_soundtracks))
            self.logmsg(f"Moved '{moving['name']}' from {src_st_id:04x} to {dst_st_id:04x}.")
        finally:
            img.close()
        return True

    # ---- Granular track editing ----

    def _rewrite_soundtrack(self, e, st_id, tracks):
        """
        Rewrite one soundtrack's folder and song list from scratch, assigning
        clean local indices 0..N-1 so the dashboard invariant always holds.

        `tracks` is the desired ordered list of songs. Each track is a dict with:
          - "name", "duration_ms"
          - exactly one of:
              "existing_song_id" : the composite id of a song already on disk in
                                   this soundtrack's folder (its bytes are reused)
              "wma_path"         : path to a NEW already-converted WMA on the host
        Returns the new list of song dicts (with fresh composite ids) for ST.DB.

        This is the single place that maintains the local-index invariant; all
        granular edits (add/remove track) funnel through here.
        """
        folder = f"{TDATA_MUSIC}/{st_id:04x}"

        # 1. Read the bytes of every track we need to keep BEFORE wiping anything.
        payloads = []  # list of (name, duration_ms, bytes)
        for t in tracks:
            if "wma_path" in t:
                with open(t["wma_path"], "rb") as fh:
                    data = fh.read()
            else:
                old_cid = t["existing_song_id"]
                data = e.read_file(f"{folder}/{old_cid:08x}.wma")
            payloads.append((t["name"], t["duration_ms"], data))

        # 2. Wipe the folder (if present) so we can rewrite with clean indices.
        try:
            e.delete_dir(folder)
        except fatx.FatxError:
            pass

        # 3. Write each track back with local index 0..N-1.
        new_songs = []
        for local_id, (name, dur, data) in enumerate(payloads):
            composite = ((st_id & 0xFFFF) << 16) | (local_id & 0xFFFF)
            e.write_file(f"{folder}/{composite:08x}.wma", data)
            new_songs.append({"name": name, "duration_ms": dur, "song_id": composite})
        return new_songs

    def _do_remove_track(self, st_id, song_id):
        """Remove a single track from a soundtrack, renumbering the rest."""
        img = fatx.XboxImage(self.raw_path, writable=True)
        e = img.partition("E")
        try:
            all_soundtracks = self._read_existing_soundtracks(e)
            target = next((s for s in all_soundtracks if s.get("st_id") == st_id), None)
            if not target:
                self.logmsg(f"Soundtrack {st_id:04x} not found.")
                return False

            kept = [s for s in target["songs"] if s["song_id"] != song_id]
            if len(kept) == len(target["songs"]):
                self.logmsg("Track not found.")
                return False

            if not kept:
                # Removing the last track removes the whole soundtrack.
                self.logmsg("Last track removed — removing the soundtrack.")
                img.close()
                return self._do_remove(st_id)

            # Build the desired track list (reuse existing bytes), rewrite folder.
            desired = [{"name": s["name"], "duration_ms": s["duration_ms"],
                        "existing_song_id": s["song_id"]} for s in kept]
            target["songs"] = self._rewrite_soundtrack(e, st_id, desired)
            e.write_file(f"{TDATA_MUSIC}/ST.DB", st.build_stdb(all_soundtracks))
            self.logmsg(f"Removed track; '{target['name']}' now has {len(kept)} track(s).")
        finally:
            img.close()
        return True

    def _do_add_tracks(self, st_id, audio_paths, progress=None):
        """Add one or more audio files to an existing soundtrack."""
        img = fatx.XboxImage(self.raw_path, writable=True)
        e = img.partition("E")
        try:
            all_soundtracks = self._read_existing_soundtracks(e)
            target = next((s for s in all_soundtracks if s.get("st_id") == st_id), None)
            if not target:
                self.logmsg(f"Soundtrack {st_id:04x} not found.")
                return False

            tmp_wma = os.path.join(self.work_dir, "addwma")
            os.makedirs(tmp_wma, exist_ok=True)

            # Existing tracks keep their bytes; new ones get converted.
            desired = [{"name": s["name"], "duration_ms": s["duration_ms"],
                        "existing_song_id": s["song_id"]} for s in target["songs"]]

            added = 0
            for i, ap in enumerate(audio_paths):
                base = os.path.basename(ap)
                dst = os.path.join(tmp_wma, f"add_{i:04d}.wma")
                if st.convert_to_wma(ap, dst, codec=self.codec.get()):
                    desired.append({"name": st.clean_track_name(base),
                                    "duration_ms": st.ffprobe_duration_ms(dst),
                                    "wma_path": dst})
                    self.logmsg(f"  + {base}")
                    added += 1
                else:
                    self.logmsg(f"  skip (convert failed): {base}")
                if progress:
                    progress(int((i + 1) / len(audio_paths) * 70), 100)

            if not added:
                self.logmsg("No tracks were added.")
                return False

            target["songs"] = self._rewrite_soundtrack(e, st_id, desired)
            e.write_file(f"{TDATA_MUSIC}/ST.DB", st.build_stdb(all_soundtracks))
            self.logmsg(f"Added {added} track(s); '{target['name']}' now has "
                        f"{len(target['songs'])} track(s).")
        finally:
            img.close()
        return True

    def _next_song_id(self, partition):
        import struct
        try: return struct.unpack_from("<i", partition.read_file(TDATA_MUSIC + "/ST.DB"), 412)[0]
        except Exception: return 0

    def _read_existing_soundtracks(self, partition):
        import struct
        out = []
        try: data = partition.read_file(TDATA_MUSIC + "/ST.DB")
        except Exception: return out
        try:
            num = struct.unpack_from("<i", data, 4)[0]
            groups = []
            off = 0xCA00
            while off + 512 <= len(data):
                if struct.unpack_from("<i", data, off)[0] != st.MAGIC_SONG_GROUP: break
                sids = struct.unpack_from("<6i", data, off + 16)
                stimes = struct.unpack_from("<6i", data, off + 40)
                snames = [data[off+64+i*64:off+128+i*64].decode("utf-16-le").rstrip("\x00") for i in range(6)]
                groups.append((sids, stimes, snames))
                off += 512

            for idx in range(num):
                soff = 0x0200 + idx * 512
                if struct.unpack_from("<i", data, soff)[0] != st.MAGIC_SOUNDTRACK: continue
                real_st_id = struct.unpack_from("<i", data, soff + 4)[0]
                num_songs = struct.unpack_from("<I", data, soff + 8)[0]
                gi = struct.unpack_from("<84i", data, soff + 12)
                name = data[soff+352:soff+448].decode("utf-16-le").rstrip("\x00")
                songs = []
                for g in range((num_songs + 5) // 6):
                    sids, stimes, snames = groups[gi[g]]
                    for k in range(6):
                        if len(songs) < num_songs:
                            songs.append({"name": snames[k], "duration_ms": stimes[k], "song_id": sids[k]})
                out.append({"name": name, "songs": songs, "st_id": real_st_id})
        except Exception: pass
        return out

    def save_image(self):
        """Save (overwrite) the originally-loaded image, with confirmation."""
        if self.busy or not self.raw_path or not self.image_path:
            return
        if not messagebox.askyesno(
                "Save (overwrite)",
                f"Overwrite the original image?\n\n{self.image_path}\n\n"
                "The file is written safely (to a temporary file first, then "
                "swapped in), so an interrupted save won't corrupt the original."):
            return
        self._write_image(self.image_path, overwrite_original=True)

    def save_image_as(self):
        """Save to a new path chosen by the user, defaulting to the image's
        own directory and filename."""
        if self.busy or not self.raw_path:
            return
        initial_dir = os.path.dirname(self.image_path) if self.image_path else None
        initial_file = os.path.basename(self.image_path) if self.image_path else (
            "xbox_hdd.qcow2" if self.is_qcow2 else "xbox_hdd.img")
        out = filedialog.asksaveasfilename(
            title="Save image as",
            defaultextension=".qcow2" if self.is_qcow2 else ".img",
            initialdir=initial_dir,
            initialfile=initial_file,
            filetypes=[("qcow2 image", "*.qcow2"), ("Raw image", "*.img")])
        if not out:
            return
        self._write_image(out, overwrite_original=False)

    def _write_image(self, dest_path, overwrite_original):
        """Shared image-writing core. Recompresses to qcow2 or copies raw,
        writing to a temp file first then atomically replacing the destination."""
        self.set_busy(True, "Saving…")
        self.progress.config(mode="determinate", value=0, maximum=100)
        want_qcow2 = dest_path.lower().endswith(".qcow2")

        def work(progress):
            # Write to a temp file in the same directory, then os.replace() it
            # into place — atomic on the same filesystem, so an interrupted
            # write can never leave a half-written destination.
            dest_dir = os.path.dirname(os.path.abspath(dest_path)) or "."
            os.makedirs(dest_dir, exist_ok=True)
            tmp_out = os.path.join(dest_dir, f".{os.path.basename(dest_path)}.tmp")
            try:
                if want_qcow2:
                    self.logmsg("Recompressing raw → qcow2…")
                    qcow2.raw_to_qcow2(self.raw_path, tmp_out,
                                       progress=lambda c, t: progress(c, t))
                else:
                    self.logmsg("Writing raw image…")
                    shutil.copy2(self.raw_path, tmp_out)
                os.replace(tmp_out, dest_path)
            except Exception:
                # Clean up the temp file on failure so we don't litter
                try:
                    if os.path.exists(tmp_out):
                        os.remove(tmp_out)
                except Exception:
                    pass
                raise
            return dest_path

        def done(path):
            self.progress.config(value=self.progress["maximum"])
            self.logmsg(f"Saved: {path}")
            self.set_busy(False, "Ready")
            if overwrite_original:
                messagebox.showinfo("Saved", f"Overwrote:\n{path}")
            else:
                messagebox.showinfo(
                    "Saved",
                    f"Image saved to:\n{path}\n\n"
                    "Copy it back to your device to use the updated soundtracks.")

        def err(e, tb):
            self.set_busy(False, "Error")
            self.logmsg("ERROR: " + str(e))
            messagebox.showerror("Save failed", str(e))

        def prog(c, t):
            self.progress.config(maximum=t, value=c)
            self.update_idletasks()

        Task(work, lambda r: self.after(0, done, r),
             lambda e, tb: self.after(0, err, e, tb),
             on_progress=lambda c, t: self.after(0, prog, c, t)).start()

    def destroy(self):
        try: shutil.rmtree(self.work_dir, ignore_errors=True)
        finally: super().destroy()

def cleanup_orphaned_temp_dirs():
    temp_base = tempfile.gettempdir()
    for item in os.listdir(temp_base):
        if item.startswith("xbst_"):
            try: shutil.rmtree(os.path.join(temp_base, item), ignore_errors=True)
            except Exception: pass

if __name__ == "__main__":
    cleanup_orphaned_temp_dirs()
    App().mainloop()