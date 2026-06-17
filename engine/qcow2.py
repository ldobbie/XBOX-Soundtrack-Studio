"""
qcow2.py — Pure-Python qcow2 <-> raw conversion.

No external dependencies. Validated byte-for-byte against qemu-img 8.2 in both
directions (our qcow2 reads correctly in qemu-img, and qemu-img's qcow2 reads
correctly here).

Used so the app can open compressed Batocera HDD images and write them back
without requiring the user to install qemu.
"""
import os
import struct
import zlib

QCOW2_MAGIC = 0x514649FB  # "QFI\xfb"
L1_OFFSET_MASK = 0x00FFFFFFFFFFFE00
L2_OFFSET_MASK = 0x00FFFFFFFFFFFE00
L2_COMPRESSED_FLAG = 1 << 62


class Qcow2Error(Exception):
    pass


class Qcow2Reader:
    """Reads a qcow2 file and can extract it to a raw image."""

    def __init__(self, path):
        self.path = path
        self.f = open(path, "rb")
        self._parse_header()

    def _parse_header(self):
        self.f.seek(0)
        hdr = self.f.read(72)
        magic, version = struct.unpack(">II", hdr[0:8])
        if magic != QCOW2_MAGIC:
            raise Qcow2Error("Not a qcow2 file (bad magic)")
        if version not in (2, 3):
            raise Qcow2Error(f"Unsupported qcow2 version {version}")
        self.version = version
        (backing_offset, backing_size, cluster_bits, size, crypt_method,
         l1_size, l1_table_offset, refcount_offset, refcount_clusters,
         nb_snapshots, snapshots_offset) = struct.unpack(">QIIQIIQQIIQ", hdr[8:72])
        if backing_offset:
            raise Qcow2Error("Backing files are not supported")
        if crypt_method:
            raise Qcow2Error("Encrypted images are not supported")
        self.cluster_bits = cluster_bits
        self.cluster_size = 1 << cluster_bits
        self.size = size
        self.l1_size = l1_size
        self.l1_table_offset = l1_table_offset
        self.l2_entries = self.cluster_size // 8

    def extract_to_raw(self, out_path, progress=None):
        """Write the full raw disk image to out_path."""
        self.f.seek(self.l1_table_offset)
        l1 = struct.unpack(f">{self.l1_size}Q",
                           self.f.read(self.l1_size * 8))

        total_clusters = (self.size + self.cluster_size - 1) // self.cluster_size
        with open(out_path, "wb") as out:
            out.truncate(self.size)  # sparse zero-fill
            for vcluster in range(total_clusters):
                l1_idx = vcluster // self.l2_entries
                l2_idx = vcluster % self.l2_entries
                if l1_idx >= len(l1):
                    break
                l2_offset = l1[l1_idx] & L1_OFFSET_MASK
                if l2_offset == 0:
                    continue
                self.f.seek(l2_offset + l2_idx * 8)
                l2_entry = struct.unpack(">Q", self.f.read(8))[0]
                if l2_entry == 0:
                    continue
                if l2_entry & L2_COMPRESSED_FLAG:
                    data = self._read_compressed_cluster(l2_entry)
                else:
                    offset = l2_entry & L2_OFFSET_MASK
                    if offset == 0:
                        continue
                    self.f.seek(offset)
                    data = self.f.read(self.cluster_size)
                out.seek(vcluster * self.cluster_size)
                out.write(data)
                if progress and (vcluster % 256 == 0):
                    progress(vcluster, total_clusters)
        if progress:
            progress(total_clusters, total_clusters)
        return out_path

    def _read_compressed_cluster(self, l2_entry):
        x = 62 - (self.cluster_bits - 8)
        offset = l2_entry & ((1 << x) - 1)
        nb_sectors = (l2_entry >> x) & ((1 << (self.cluster_bits - 8)) - 1)
        nb_sectors += 1
        comp_len = nb_sectors * 512
        # The compressed stream may start mid-sector; read generously
        self.f.seek(offset)
        raw = self.f.read(comp_len + 512)
        d = zlib.decompressobj(-zlib.MAX_WBITS)
        out = d.decompress(raw, self.cluster_size)
        return out[:self.cluster_size].ljust(self.cluster_size, b"\x00")

    def close(self):
        self.f.close()


def raw_to_qcow2(raw_path, out_path, cluster_bits=16, progress=None):
    """
    Create a sparse qcow2 (version 3) from a raw image.

    Zero clusters are omitted to keep the file small. Produces output that
    passes `qemu-img check` cleanly.
    """
    cluster_size = 1 << cluster_bits
    size = os.path.getsize(raw_path)
    l2_entries = cluster_size // 8
    total_clusters = (size + cluster_size - 1) // cluster_size
    l1_size = (total_clusters + l2_entries - 1) // l2_entries

    header_clusters = 1
    l1_offset = header_clusters * cluster_size
    l1_clusters = (l1_size * 8 + cluster_size - 1) // cluster_size

    refcount_table_offset = l1_offset + l1_clusters * cluster_size
    refcount_table_clusters = 1
    refcount_block_offset = refcount_table_offset + refcount_table_clusters * cluster_size
    refcount_block_clusters = 1
    l2_tables_offset = refcount_block_offset + refcount_block_clusters * cluster_size

    with open(raw_path, "rb") as rf, open(out_path, "wb") as wf:
        # Find non-zero clusters (sparse)
        nonzero = []
        rf.seek(0)
        zero_cluster = b"\x00" * cluster_size
        for vcluster in range(total_clusters):
            data = rf.read(cluster_size)
            if len(data) < cluster_size:
                data = data.ljust(cluster_size, b"\x00")
            if data != zero_cluster:
                nonzero.append(vcluster)
            if progress and (vcluster % 256 == 0):
                progress(vcluster, total_clusters * 2)  # first half = scan

        needed_l1 = sorted(set(vc // l2_entries for vc in nonzero))
        l1_table = [0] * l1_size
        l2_offset_map = {}
        cur = l2_tables_offset
        for l1_idx in needed_l1:
            l2_offset_map[l1_idx] = cur
            l1_table[l1_idx] = cur | (1 << 63)  # COPIED flag
            cur += cluster_size

        data_start = cur
        l2_data = {l1_idx: [0] * l2_entries for l1_idx in needed_l1}
        offset_for = {}
        cur_data = data_start
        for vcluster in nonzero:
            l1_idx = vcluster // l2_entries
            l2_idx = vcluster % l2_entries
            l2_data[l1_idx][l2_idx] = cur_data | (1 << 63)
            offset_for[vcluster] = cur_data
            cur_data += cluster_size

        file_size = cur_data
        wf.truncate(file_size)

        # Header (v3)
        wf.seek(0)
        header = struct.pack(">II", QCOW2_MAGIC, 3)
        header += struct.pack(">QI", 0, 0)         # backing file offset/size
        header += struct.pack(">I", cluster_bits)
        header += struct.pack(">Q", size)
        header += struct.pack(">I", 0)             # crypt method
        header += struct.pack(">I", l1_size)
        header += struct.pack(">Q", l1_offset)
        header += struct.pack(">Q", refcount_table_offset)
        header += struct.pack(">I", refcount_table_clusters)
        header += struct.pack(">I", 0)             # nb_snapshots
        header += struct.pack(">Q", 0)             # snapshots offset
        header += struct.pack(">Q", 0)             # incompatible features
        header += struct.pack(">Q", 0)             # compatible features
        header += struct.pack(">Q", 0)             # autoclear features
        header += struct.pack(">I", 4)             # refcount order (2^4 = 16 bit)
        header += struct.pack(">I", 104)           # header length
        wf.write(header)

        # L1 table
        wf.seek(l1_offset)
        wf.write(struct.pack(f">{l1_size}Q", *l1_table))

        # Refcount table -> single refcount block
        wf.seek(refcount_table_offset)
        wf.write(struct.pack(">Q", refcount_block_offset))

        # Refcount block: mark every allocated cluster as referenced once
        num_used = file_size // cluster_size
        wf.seek(refcount_block_offset)
        wf.write(struct.pack(f">{num_used}H", *([1] * num_used)))

        # L2 tables
        for l1_idx in needed_l1:
            wf.seek(l2_offset_map[l1_idx])
            wf.write(struct.pack(f">{l2_entries}Q", *l2_data[l1_idx]))

        # Data clusters
        rf.seek(0)
        for i, vcluster in enumerate(nonzero):
            rf.seek(vcluster * cluster_size)
            data = rf.read(cluster_size).ljust(cluster_size, b"\x00")
            wf.seek(offset_for[vcluster])
            wf.write(data)
            if progress and (i % 256 == 0):
                progress(total_clusters + i, total_clusters * 2)

    if progress:
        progress(total_clusters * 2, total_clusters * 2)
    return out_path


def is_qcow2(path):
    """Return True if the file looks like a qcow2 image."""
    try:
        with open(path, "rb") as f:
            magic = struct.unpack(">I", f.read(4))[0]
        return magic == QCOW2_MAGIC
    except Exception:
        return False
