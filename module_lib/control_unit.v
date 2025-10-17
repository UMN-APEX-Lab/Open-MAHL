module control_unit (
    input [31:0] instruction,
    output reg [3:0] alu_control,
    output reg mem_read,
    output reg mem_write,
    output reg reg_write
);

    always @(*) begin
        // Default values for control signals
        alu_control = 4'b0000;
        mem_read = 1'b0;
        mem_write = 1'b0;
        reg_write = 1'b0;

        // Decode the instruction to generate control signals
        case (instruction[6:0])  // RISC-V opcode
            7'b0110011: begin
                // Example: R-type instruction
                alu_control = {1'b0, instruction[14:12]};  // Corrected to include funct3 properly
                reg_write = 1'b1;
            end
            7'b0000011: begin
                // Example: Load instruction
                alu_control = 4'b0010;  // Example ALU operation
                mem_read = 1'b1;
                reg_write = 1'b1;
            end
            7'b0100011: begin
                // Example: Store instruction
                alu_control = 4'b0010;  // Example ALU operation
                mem_write = 1'b1;
            end
            // Add other cases for different instruction types as needed
            default: begin
                // Default case for unsupported instructions
                alu_control = 4'b0000;
                mem_read = 1'b0;
                mem_write = 1'b0;
                reg_write = 1'b0;
            end
        endcase
    end

endmodule
