module riscv_mips_core (
    input wire [31:0] instruction,
    input wire clk,
    input wire reset,
    output wire [31:0] result
);

    // Internal signals
    wire [31:0] control_signals;
    wire [31:0] alu_result;
    wire [31:0] read_data1, read_data2;
    wire [3:0] alu_control;
    wire mem_read, mem_write, reg_write;
    wire [31:0] mem_read_data;
    wire [31:0] stage_out;
    wire stall;
    wire forward_a, forward_b;

    // Decoding the instruction
    riscv_decoder riscv_dec (
        .instruction(instruction),
        .control_signals(control_signals)
    );

    // Control unit
    control_unit ctrl_unit (
        .instruction(instruction),
        .alu_control(alu_control),
        .mem_read(mem_read),
        .mem_write(mem_write),
        .reg_write(reg_write)
    );

    // Register file
    register_file reg_file (
        .write_data(alu_result),
        .write_enable(reg_write),
        .read_reg1(instruction[19:15]),
        .read_reg2(instruction[24:20]),
        .write_reg(instruction[11:7]),
        .read_data1(read_data1),
        .read_data2(read_data2)
    );

    // ALU operation
    riscv_alu riscv_alu_inst (
        .op1(read_data1),
        .op2((forward_a) ? alu_result : read_data2), // Apply forwarding if needed
        .func_code(alu_control),
        .result(alu_result)
    );

    // Memory interface
    memory_interface mem_interface (
        .address(alu_result),
        .write_data(read_data2),
        .mem_read(mem_read),
        .mem_write(mem_write),
        .read_data(mem_read_data)
    );

    // Pipeline stage
    pipeline_stage pipeline (
        .instruction(instruction),
        .control_signals(control_signals),
        .stage_out(stage_out)
    );

    // Hazard detection
    hazard_detection hazard_detect (
        .current_instruction(instruction),
        .next_instruction(stage_out),
        .stall(stall)
    );

    // Forwarding unit
    forwarding_unit fwd_unit (
        .ex_mem_reg(stage_out[11:7]), // Assuming rd is in bits [11:7]
        .mem_wb_reg(mem_read_data[11:7]),
        .forward_a(forward_a),
        .forward_b(forward_b)
    );

    // Output assignment
    assign result = alu_result;

endmodule
