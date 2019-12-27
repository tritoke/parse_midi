#!/usr/bin/env python
from math import ceil
from struct import unpack, pack
import itertools as it


BPM = 120


def take(n, iterator):
    return [next(iterator) for _ in range(n)]


def check_mask(byte, mask):
    return byte & 0xF0 == mask


def read_chunk(midi):
    length, = unpack(">I", midi.read(4))
    data = midi.read(length)
    return data


def parse_header(chunk):
    items = unpack(">HHH", chunk)
    return items


def iter_midi(midi):
    while True:
        chunk_type = midi.read(4)
        if not chunk_type:
            break
        yield chunk_type, read_chunk(midi)


def read_variable_width_quantity(elem, track):
    elems = []
    while True:
        elems.append(elem)
        if not (elem & (1<<7)) == 1<<7:
            break
        elem = next(track)

    return int("".join(map("{0:07b}".format, elems)), 2)


def parse_sysex(track):
    length = read_variable_width_quantity(next(track), track)
    return bytes(take(length, track))


def parse_meta(track):
    meta_type = next(track)

    length = read_variable_width_quantity(next(track), track)
    if meta_type == 0x2F: # end of track
        return meta_type, b""
    return meta_type, bytes(take(length, track))


def parse_channel_mode(p_byte, track):
    return bytes([p_byte, next(track)])


def parse_channel_voice(p_byte, byte, track):
    channel = (p_byte & 0xF) + 1

    name_mapping = {
        0x80: "note_off",
        0x90: "note_on",
        0xA0: "polyphonic_key_pressure",
        0xB0: "controller_change",
        0xC0: "program_change",
        0xD0: "channel_key_pressure",
        0xE0: "pitch_bend",
    }

    for mask in name_mapping:
        if check_mask(p_byte, mask):
            name = name_mapping[mask]
            break

    if check_mask(p_byte, 0xC0) or check_mask(p_byte, 0xD0): # key pressure - program change
        return (name, channel, byte)
    else: # note off / on - key pressure - controller message - pitch bend
        return (name, channel, byte, next(track))

def iter_events(track_data):
    track = iter(track_data)
    for byte in track:
        delta_time = read_variable_width_quantity(byte, track)
        byte = next(track)
        if byte in {0xF0, 0xF7}:
            yield "sysex", delta_time, parse_sysex(track)
        elif byte == 0xFF:
            yield "meta", delta_time, parse_meta(track)
        elif check_mask(byte, 0xB0):
            n_byte = next(track)
            if n_byte in range(0x78, 0x80):
                yield "channel_mode", delta_time, parse_channel_mode(n_byte, track)
            else:
                yield "channel_voice", delta_time, parse_channel_voice(byte, n_byte, track)
        elif any(map(lambda x: check_mask(byte, x), (0x80, 0x90, 0xA0, 0xC0, 0xD0, 0xE0))):
            yield "channel_voice", delta_time, parse_channel_voice(byte, next(track), track)
        else:
            yield "unknown_byte", delta_time, byte


def int_to_note(note):
    """
    takes the midi note and returns the value
    which should be stored in buzzer_input
    to reproduce the same note.
    All notes are played for one tenth of a second.
    """
    MIDDLE_C = 0x3C
    diff = note - MIDDLE_C
    octave = 4 + (diff // 12 if diff > 0 else 0)
    note = diff % 12
    return f"81{octave}{note:x}"


def play_note(note_num, duration_num, jump_num):
    return "\n".join([
        f"\t\tLDA\tnote_{note_num}",
         "\t\tSTA\tnote",
        f"\t\tLDA\tduration_{duration_num}",
         "\t\tSTA\tduration",
        f"\t\tLDA\tjump_{jump_num}",
         "\t\tJMP\tplay_note",
        f"link_{jump_num}",
    ])


def ticks_to_tenths(ticks, us_per_tick):
    return ceil((ticks * us_per_tick) / 1e5)


with open("megalovania.mid", "rb") as midi:
    code = [
        "ORG\t0",
    ]
    notes = {}
    note_num = 1
    durations = {}
    duration_num = 1
    jump_num = 1

    for chunk_type, chunk_data in iter_midi(midi):
        if chunk_type == b"MThd":
            f_format, no_tracks, ticks_per_quarter = parse_header(chunk_data)
            us_per_tick = 60_000 // (BPM * ticks_per_quarter)
            print(f"format: {f_format}\ntracks: {no_tracks}\nticks_per_quarter: {ticks_per_quarter}")

        elif chunk_type == b"MTrk":
            for event_type, delta_time, data in iter_events(chunk_data):
                if event_type == "meta":
                    meta_type, metadata = data
                    if meta_type == 0x51: # set tempo
                        us_per_quarter, = unpack(">I", b"\0" + metadata)
                        us_per_tick = us_per_quarter / ticks_per_quarter

                elif event_type == "channel_voice":
                    name, ev_channel, *data = data
                    if name.startswith("note"):
                        note, _ = data

                        duration = ticks_to_tenths(delta_time, us_per_tick)
                        if duration not in durations:
                            durations[duration] = duration_num
                            duration_num += 1

                        note_code = int_to_note(note)
                        if note_code not in notes:
                            notes[note_code] = note_num
                            note_num += 1

                        code.append(play_note(notes[note_code], durations[duration], jump_num))
                        jump_num += 1
