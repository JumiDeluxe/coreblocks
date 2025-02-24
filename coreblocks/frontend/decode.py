from amaranth import *

from coreblocks.params.isa import Funct3
from coreblocks.params.optypes import OpType
from transactron import Method, Transaction, TModule
from ..params import GenParams
from .decoder import InstrDecoder
from coreblocks.params import *


class Decode(Elaboratable):
    """
    Simple decode unit. This is a transactional interface which instantiates a
    submodule `InstrDecoder`. This `InstrDecoder` makes actual decoding in
    a combinatorial manner.

    """

    def __init__(self, gen_params: GenParams, get_raw: Method, push_decoded: Method) -> None:
        """
        Parameters
        ----------
        gen_params : GenParams
            Instance of GenParams with parameters which should be used to generate
            fetch unit.
        get_raw : Method
            Method which is invoked to get raw instruction from previous step
            (e.g. from fetch unit) it uses `FetchLayout`.
        push_decoded : Method
            Method which is invoked to send decoded data to the next step.
            It has layout as described by `DecodeLayouts`.
        """
        self.gp = gen_params
        self.get_raw = get_raw
        self.push_decoded = push_decoded

    def elaborate(self, platform):
        m = TModule()

        m.submodules.instr_decoder = instr_decoder = InstrDecoder(self.gp)

        with Transaction().body(m):
            raw = self.get_raw(m)

            m.d.top_comb += instr_decoder.instr.eq(raw.instr)

            # Jump-branch unit requires information if the instruction was
            # decoded from a compressed instruction. To avoid adding a new signal
            # to the pipeline, we pack it in funct7 - it is not used in jb unit anyway.
            # This is a temporary hack and should be removed when we onboard the new
            # amaranth data lib and make use of it.
            is_jb_unit_instr = (
                (instr_decoder.optype == OpType.JAL)
                | (instr_decoder.optype == OpType.JALR)
                | (instr_decoder.optype == OpType.BRANCH)
            )

            exception_override = Signal()
            m.d.comb += exception_override.eq(instr_decoder.illegal | raw.access_fault)
            exception_funct = Signal(Funct3)
            with m.If(raw.access_fault):
                m.d.comb += exception_funct.eq(Funct3._EINSTRACCESSFAULT)
            with m.Elif(instr_decoder.illegal):
                m.d.comb += exception_funct.eq(Funct3._EILLEGALINSTR)

            self.push_decoded(
                m,
                {
                    "exec_fn": {
                        "op_type": Mux(~exception_override, instr_decoder.optype, OpType.EXCEPTION),
                        # imm muxing in FUs depend on unused functs set to 0
                        # todo: this is a bit awkward and needs a refactor in the future
                        "funct3": Mux(
                            ~exception_override, Mux(instr_decoder.funct3_v, instr_decoder.funct3, 0), exception_funct
                        ),
                        "funct7": Mux(
                            ~exception_override,
                            Mux(instr_decoder.funct7_v, instr_decoder.funct7, Mux(is_jb_unit_instr, raw.rvc, 0)),
                            0,
                        ),
                    },
                    "regs_l": {
                        # read/writes to phys reg 0 make no effect
                        "rl_dst": Mux(instr_decoder.rd_v & (~exception_override), instr_decoder.rd, 0),
                        "rl_s1": Mux(instr_decoder.rs1_v & (~exception_override), instr_decoder.rs1, 0),
                        "rl_s2": Mux(instr_decoder.rs2_v & (~exception_override), instr_decoder.rs2, 0),
                    },
                    "imm": instr_decoder.imm,
                    "csr": instr_decoder.csr,
                    "pc": raw.pc,
                },
            )

        return m
