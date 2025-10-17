module forwarding_unit(
    input wire [4:0] ex_mem_reg,
    input wire [4:0] mem_wb_reg,
    output reg forward_a,
    output reg forward_b
);

    always @(*) begin
        // Default forwarding values
        forward_a = 1'b0;
        forward_b = 1'b0;

        // Check for data hazards and set forwarding paths
        if (ex_mem_reg != 5'b00000) begin
            if (ex_mem_reg == mem_wb_reg) begin
                forward_a = 1'b1;
                forward_b = 1'b1;
            end
        end
    end

endmodule
