module register_file (
    input [31:0] write_data,
    input write_enable,
    input [4:0] read_reg1,
    input [4:0] read_reg2,
    input [4:0] write_reg,
    output [31:0] read_data1,
    output [31:0] read_data2
);

    // Register array to store 32 registers each of 32-bit wide
    reg [31:0] registers [31:0];

    // Reading data from the register file
    assign read_data1 = registers[read_reg1];
    assign read_data2 = registers[read_reg2];

    // Writing data to the register file
    always @(posedge clk) begin
        if (write_enable) begin
            registers[write_reg] <= write_data; // Corrected write to write_reg
        end
    end

endmodule
