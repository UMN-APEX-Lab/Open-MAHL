module mips_alu (
    input [31:0] op1,
    input [31:0] op2,
    input [3:0] func_code,
    output reg [31:0] result
);

    always @(*) begin
        case (func_code)
            4'b0000: result = op1 & op2;  // AND operation
            4'b0001: result = op1 | op2;  // OR operation
            4'b0010: result = op1 + op2;  // ADD operation
            4'b0110: result = op1 - op2;  // SUBTRACT operation
            4'b0111: result = (op1 < op2) ? 32'b1 : 32'b0;  // SET LESS THAN
            4'b1100: result = ~(op1 | op2);  // NOR operation
            default: result = 32'b0;  // Default case
        endcase
    end

endmodule
