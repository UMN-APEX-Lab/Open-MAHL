module hazard_detection (
    input [31:0] current_instruction,
    input [31:0] next_instruction,
    output reg stall
);

    always @(*) begin
        // Default stall signal to 0
        stall = 0;
        
        // Extract relevant fields from instructions
        // Assuming R-type instruction format: opcode (31:26), rs (25:21), rt (20:16), rd (15:11)
        // Assuming I-type instruction format: opcode (31:26), rs (25:21), rt (20:16), immediate (15:0)
        
        // Example: Check if the destination register of the current instruction is used as a source in the next instruction
        if ((current_instruction[11:7] == next_instruction[19:15]) ||
            (current_instruction[11:7] == next_instruction[24:20])) begin
            stall = 1; // Set stall if a hazard is detected
        end
    end

endmodule
