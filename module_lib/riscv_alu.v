module riscv_alu (
    input  [31:0] op1,
    input  [31:0] op2,
    input  [3:0]  func_code,
    output reg [31:0] result
);

    always @(*) begin
        case (func_code)
            4'b0000: result = op1 + op2;  // ADD
            4'b0001: result = op1 - op2;  // SUB
            4'b0010: result = op1 & op2;  // AND
            4'b0011: result = op1 | op2;  // OR
            4'b0100: result = op1 ^ op2;  // XOR
            4'b0101: result = op1 << op2; // SLL
            4'b0110: result = op1 >> op2; // SRL
            4'b0111: result = $signed(op1) >>> op2; // SRA
            4'b1000: result = (op1 < op2) ? 32'b1 : 32'b0; // SLT
            default: result = 32'b0; // Default case
        endcase
    end

endmodule
